// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use std::{fs, path::PathBuf, process::Command};
use autoyou_client_bindings::FrameQueue;

#[test]
fn generated_python_binding_has_binary_ownership_and_shutdown_safety() {
    // Referencing the library makes Cargo compile the cdylib as an intrinsic
    // test dependency. This is not an application/distribution build.
    FrameQueue::new().shutdown().unwrap();
    let root = PathBuf::from(std::env::var("AUTOYOU_TEST_ROOT").expect("isolated test root required"));
    let unique = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let out = root.join(format!("binding-qualification-{}-{unique}", std::process::id()));
    fs::create_dir_all(&out).unwrap();
    let filename = format!("{}autoyou_client_bindings{}", std::env::consts::DLL_PREFIX, std::env::consts::DLL_SUFFIX);
    let library = std::env::current_exe().unwrap().parent().unwrap().join(&filename);
    assert!(library.is_file(), "Cargo's test cdylib is required");
    uniffi::generate(uniffi::GenerateOptions {
        languages: vec![uniffi::TargetLanguage::Python, uniffi::TargetLanguage::Swift, uniffi::TargetLanguage::Kotlin],
        source: library.to_string_lossy().into_owned().into(),
        out_dir: out.to_string_lossy().into_owned().into(),
        format: false, metadata_no_deps: true, ..Default::default()
    }).unwrap();
    fs::copy(&library, out.join(filename)).unwrap();
    let script = r#"
import gc, socket, sys
import json, time, hashlib, os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import asyncio
import runpy
# Windows creates the event loop's self-pipe with an ephemeral loopback socket.
# Construct it before denying all Python network operations in this fixture.
qualification_loop = asyncio.new_event_loop()
def deny(*args, **kwargs):
    raise AssertionError('binding qualification must not contact a network')
socket.socket.connect = socket.socket.connect_ex = socket.socket.bind = deny
import autoyou_client_bindings as api
from shared.iroh_binding import load_binding, target_tag, library_name
info = api.core_info()
root = Path.cwd()
manifest = dict(schema=1, api_version=1, wire_version=1, core_version='0.1.0', iroh_version='1.3.0',
    noq_version='1.3.0', uniffi_version='0.32.2', rust_toolchain='1.99.0', target_tag=target_tag(),
    lock_sha256=info.lock_sha256,
    files={name: hashlib.sha256((root/name).read_bytes()).hexdigest()
        for name in ['autoyou_client_bindings.py', library_name()]})
(root/'binding-manifest.json').write_text(json.dumps(manifest))
api = load_binding(artifact_root=root)
queue = api.FrameQueue()
original = bytearray([0, 255, 13, 10])
queue.send(api.TransportFrame(lane=7, generation=19, stream_id=1, sequence=0, payload=bytes(original)), None)
original[:] = b'xxxx'
frame = queue.poll(0, 1)[0]
assert frame.generation == 19 and bytes(frame.payload) == bytes([0, 255, 13, 10])
def send(i):
    queue.send(api.TransportFrame(lane=7, generation=i+1, stream_id=1, sequence=i, payload=bytes([i])*4096), None)
with ThreadPoolExecutor(max_workers=4) as workers:
    list(workers.map(send, range(32)))
frames = queue.poll(0, 64)
assert len(frames) == 32 and {item.generation for item in frames} == set(range(1, 33))
assert all(bytes(item.payload) == bytes([item.generation-1])*4096 for item in frames)
queue.shutdown()
try:
    queue.send(api.TransportFrame(lane=7, generation=1, stream_id=1, sequence=0, payload=b'x'), None)
except api.BindingError.Closed:
    pass
else:
    raise AssertionError('closed queue accepted a frame')
for _ in range(100):
    temporary = api.FrameQueue()
    temporary.shutdown()
    del temporary
gc.collect()
policy = json.dumps(dict(bind_addresses=['127.0.0.1:0'], local_only=True))
server = api.SharedEndpoint(policy, bytes([81])*32)
client = api.SharedEndpoint(policy, bytes([82])*32)
server_info, client_info = server.endpoint_info(), client.endpoint_info()
connection_id = client.dial(server_info.ticket, server_info.endpoint_id, False)
def wait(host, kind):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        for event in host.poll(64):
            if event.kind == kind:
                return event
        time.sleep(0.005)
    raise AssertionError('isolated native endpoint event timed out')
outgoing = wait(client, api.TransportEventKind.CONNECTED)
incoming = wait(server, api.TransportEventKind.CONNECTED)
assert outgoing.connection_id == connection_id
assert incoming.endpoint_id == client_info.endpoint_id
assert bytes(outgoing.exporter) == bytes(incoming.exporter)
context = api.AdmissionContext(exporter=bytes(outgoing.exporter), initiator_endpoint=client_info.endpoint_id,
    acceptor_endpoint=server_info.endpoint_id, challenge=bytes([83])*32, protocol='autoyou/session/1',
    capabilities_json='{"chat":true,"media":false}', generation=1, authorization_epoch=2)
binding = bytes(api.admission_binding(context))
assert len(binding) == 32
context.capabilities_json = '{"media":false,"chat":true}'
assert bytes(api.admission_binding(context)) == binding
context.generation = 2
assert bytes(api.admission_binding(context)) != binding
envelope = json.dumps(dict(header=dict(message_id='synthetic-envelope', message_type='chat', timestamp=1.0),
    payload=dict(text='synthetic-chat'))).encode()
assert api.application_lane(envelope) == 3
assert json.loads(bytes(api.validate_envelope(envelope)))['payload']['text'] == 'synthetic-chat'
try:
    api.validate_envelope(b'{"header":{"message_id":"bad","message_type":"chunk","timestamp":1.0},"payload":{}}')
except api.BindingError.InvalidInput:
    pass
else:
    raise AssertionError('legacy chunk was accepted by new binding')
def grant(endpoint, device):
    return api.SessionGrant(endpoint_id=endpoint, device_id=device, owner_id='synthetic-owner',
        conversation_id='synthetic-conversation', generation=1, authorization_epoch=2,
        expires_at_ms=int(time.time()*1000)+60000, scopes=['chat', 'files', 'media'])
server.admit(incoming.connection_id, grant(client_info.endpoint_id, 'synthetic-client'))
client.admit(connection_id, grant(server_info.endpoint_id, 'synthetic-server'))
server.activate(incoming.connection_id)
client.activate(connection_id)
media = api.MediaPacket(kind=1, codec=4, keyframe=False, media_generation=2, authorization_epoch=2,
    source_id=9, sequence=5, timestamp_us=100000, duration_us=20000, width=0, height=0,
    channels=1, data=bytes([7])*1920)
media_frame = api.encode_media_packet(1, media)
assert media_frame.generation == 1 and media_frame.stream_id == 9
try:
    client.send(connection_id, media_frame, int(time.time()*1000)+1000)
except api.BindingError.PermissionDenied:
    pass
else:
    raise AssertionError('session admission alone approved a media source')
source = dict(lease_id='synthetic-media-lease',call_id='synthetic-call',participant_id='synthetic-participant',
    target_id='synthetic-target',source_id=9,media_generation=2,authorization_epoch=2,
    expires_at_ms=int(time.time()*1000)+30000,kind=1,codec=4,sample_rate=48000,channels=1,
    width=0,height=0,fps=0,layout='single',maximum_delay_ms=2000)
try:
    client.approve_media_source(connection_id,json.dumps(dict(source,owner_id='synthetic-forged-owner')),False)
except api.BindingError.InvalidInput:
    pass
else:
    raise AssertionError('unknown source lease authority field was accepted')
client.approve_media_source(connection_id,json.dumps(source),False)
server.approve_media_source(incoming.connection_id,json.dumps(source),True)
try:
    client.send(connection_id,media_frame,None)
except api.BindingError.InvalidInput:
    pass
else:
    raise AssertionError('media frame without a transport deadline was accepted')
client.send(connection_id,media_frame,int(time.time()*1000)+1000)
received = wait(server,api.TransportEventKind.FRAME)
decoded = api.decode_media_packet(received.frame)
assert decoded.media_generation == 2 and decoded.sequence == 5 and bytes(decoded.data) == bytes([7])*1920
playout=api.MediaPlayout(grant(client_info.endpoint_id,'synthetic-client'),int(time.time()*1000))
try: playout.push(received.frame,1000000,1000000,int(time.time()*1000))
except api.BindingError.PermissionDenied: pass
else: raise AssertionError('playout inferred capture consent from admitted media scope')
playout.approve_source(json.dumps(source),int(time.time()*1000))
assert 0 < received.media_ttl_us <= 2000000 and 0 <= received.media_age_us < 2000000
assert playout.push(received.frame,1000000,1000000,int(time.time()*1000))
assert playout.feedback(9,int(time.time()*1000)).buffered_bytes==1920
played=playout.take(16,1060000,int(time.time()*1000))
assert len(played)==1 and played[0].reset_decoder and played[0].missing_audio_frames==0
assert bytes(played[0].packet.data)==bytes([7])*1920
playout.revoke_source(9)
try: playout.push(received.frame,1060001,1060001,int(time.time()*1000))
except api.BindingError.PermissionDenied: pass
else: raise AssertionError('revoked playout source reached renderer')
playout.shutdown()
client.revoke_media_source(connection_id,9,False)
server.revoke_media_source(incoming.connection_id,9,True)
try:
    client.send(connection_id,media_frame,int(time.time()*1000)+1000)
except api.BindingError.PermissionDenied:
    pass
else:
    raise AssertionError('revoked native media source continued sending')
# Exercise the common codec guard with actual installed codec output. These
# frames are generated from synthetic samples, never device capture or files.
import av
import fractions
for name, codec_id in [('libopenh264',2),('libvpx',3)]:
    encoder=av.CodecContext.create(name,'w')
    encoder.width=160;encoder.height=96;encoder.pix_fmt='yuv420p'
    encoder.time_base=fractions.Fraction(1,30);encoder.framerate=fractions.Fraction(30,1)
    encoder.bit_rate=300000;encoder.max_b_frames=0;encoder.thread_count=1
    if codec_id==3: encoder.options={'deadline':'realtime','lag-in-frames':'0','cpu-used':'8'}
    packets=[]
    for index in range(4):
        sample=av.VideoFrame(160,96,'yuv420p')
        for plane in sample.planes: plane.update(bytes([40+index])*plane.buffer_size)
        sample.pts=index;sample.time_base=encoder.time_base
        packets.extend(encoder.encode(sample))
    packets.extend(encoder.encode(None))
    assert packets and packets[0].is_keyframe
    codec_playout=api.MediaPlayout(grant(client_info.endpoint_id,'synthetic-client'),int(time.time()*1000))
    codec_playout.approve_source(json.dumps(dict(source,source_id=11,kind=3,codec=codec_id,sample_rate=0,channels=0,
        width=160,height=96,fps=30)),int(time.time()*1000))
    decoder=av.CodecContext.create('h264' if codec_id==2 else 'vp8','r');decoder.thread_count=1
    decoded_frames=0
    for index, packet in enumerate(packets):
        coded=api.MediaPacket(kind=3,codec=codec_id,keyframe=packet.is_keyframe,media_generation=2,authorization_epoch=2,
            source_id=11,sequence=index,timestamp_us=index*33333,duration_us=33333,width=160,height=96,channels=0,data=bytes(packet))
        checked=api.decode_media_packet(api.encode_media_packet(1,coded))
        assert bytes(checked.data)==bytes(packet)
        assert codec_playout.push(api.encode_media_packet(1,coded),1000000+index*33333,1000000+index*33333,int(time.time()*1000))
        if packet.is_keyframe:
            coded.width=176
            try: api.decode_media_packet(api.encode_media_packet(1,coded))
            except api.BindingError.InvalidInput: pass
            else: raise AssertionError('forged encoded video dimensions reached decoder')
    ready=codec_playout.take(16,1000000+(len(packets)-1)*33333+60000,int(time.time()*1000))
    assert len(ready)==4 and ready[0].reset_decoder
    for frame in ready: decoded_frames+=len(decoder.decode(av.Packet(bytes(frame.packet.data))))
    decoded_frames+=len(decoder.decode(None))
    assert decoded_frames==4
    codec_playout.shutdown()
encoder=av.CodecContext.create('libopus','w');encoder.sample_rate=48000;encoder.layout='mono';encoder.format='s16'
encoder.time_base=fractions.Fraction(1,48000);encoder.bit_rate=64000
sample=av.AudioFrame(format='s16',layout='mono',samples=960);sample.sample_rate=48000
sample.pts=0;sample.time_base=encoder.time_base
sample.planes[0].update(bytes([0,1])*960)
packets=encoder.encode(sample)
assert packets
for packet in packets:
    coded=api.MediaPacket(kind=1,codec=1,keyframe=False,media_generation=2,authorization_epoch=2,source_id=12,sequence=0,
        timestamp_us=0,duration_us=20000,width=0,height=0,channels=1,data=bytes(packet))
    assert bytes(api.decode_media_packet(api.encode_media_packet(1,coded)).data)==bytes(packet)
file_bytes = bytes([0, 255, 13, 10]) + b'isolated generated Python to Iroh'
file_id = 'ab'*16
descriptor = dict(transfer_id=file_id, purpose='attachment', filename='synthetic.bin', mime_type='application/octet-stream',
    total=len(file_bytes), offset=0, sha256=list(hashlib.sha256(file_bytes).digest()), expires_at_ms=int(time.time()*1000)+60000, metadata={})
scope = dict(local_endpoint=server_info.endpoint_id, remote_endpoint=client_info.endpoint_id, server_instance=server_info.endpoint_id,
    device_id='synthetic-client', owner_key='synthetic-owner', canonical_user_id='synthetic-user', conversation_key='synthetic-conversation',
    authorization_epoch=2, direction='incoming')
file_root = str(root/'synthetic-file-store')
store = api.FileTransferStore(file_root, bytes([94])*32, json.dumps(scope), True)
file_metadata = bytes(api.binary_transfer_metadata(json.dumps(descriptor), 'synthetic-file-open'))
assert json.loads(api.binary_transfer_descriptor(file_metadata)) == descriptor
writer = api.ByteStreamWriter(7, api.ByteStreamContent.RAW_FILE, len(file_bytes), file_metadata)
stream = client.allocate_byte_stream(connection_id, 7)
for payload in [writer.open_record(), writer.data_record(file_bytes), writer.finish_record()]:
    client.send(connection_id, api.TransportFrame(lane=7, generation=1, stream_id=stream, sequence=0, payload=bytes(payload)), None)
receiver = api.FileTransferReceiver(store, 1)
queried = json.loads(receiver.control(json.dumps(dict(event='query', descriptor=descriptor)), int(time.time()*1000)))
assert queried['phase'] == 'pending' and queried['offset'] == 0
completed = False
deadline = time.monotonic()+5
while not completed:
    assert time.monotonic() < deadline, 'generated file stream timed out'
    for event in server.poll(64):
        if event.kind != api.TransportEventKind.FRAME:
            continue
        record = receiver.receive(event.frame.stream_id, bytes(event.frame.payload), int(time.time()*1000))
        if record.kind == api.ByteStreamKind.FINISH:
            commit = json.loads(record.control_json)
            assert commit['phase'] == 'committed' and commit['prefix_sha256'] == descriptor['sha256']
            assert bytes(store.read(file_id, 1, 0, 49152, int(time.time()*1000))) == file_bytes
            status_wire = bytes(api.binary_transfer_control(record.control_json, 'synthetic-durable-receipt'))
            assert json.loads(api.parse_binary_transfer_control(status_wire)) == commit
            server.send(incoming.connection_id, api.TransportFrame(lane=2, generation=1, stream_id=0, sequence=0, payload=status_wire), None)
            server.acknowledge_byte_stream(incoming.connection_id, 7, record.receipt.stream_id, record.receipt.total, bytes(record.receipt.digest))
            completed = True
    time.sleep(0.005)
durable_receipts = []
for _ in range(10):
    for event in client.poll(64):
        if event.kind == api.TransportEventKind.FRAME:
            durable_receipts.append(json.loads(api.parse_binary_transfer_control(bytes(event.frame.payload))))
    time.sleep(0.005)
assert len(durable_receipts) == 1 and durable_receipts[0]['phase'] == 'committed'
receiver.shutdown()
store.release(file_id)
reopened = api.FileTransferStore(file_root, bytes([94])*32, json.dumps(scope), False)
assert reopened.checkpoint(file_id, int(time.time()*1000)).phase == api.FileTransferPhase.COMMITTED
reopened.delete(file_id, 1, int(time.time()*1000))
try:
    reopened.stage(json.dumps(descriptor), 2, int(time.time()*1000))
except api.BindingError.FileDeleted:
    pass
else:
    raise AssertionError('generated file store resurrected deleted data')
inbox = api.ApplicationDeliveryStore(str(root/'synthetic-inbox'), bytes([91])*32, json.dumps(scope), True)
outbox_scope = dict(scope, direction='outgoing')
outbox = api.ApplicationDeliveryStore(str(root/'synthetic-outbox'), bytes([92])*32, json.dumps(outbox_scope), True)
revision = inbox.revision(1)
outbox.align_revision(revision, 1, int(time.time()*1000))
prompt_wire = json.dumps(dict(header=dict(message_id='synthetic-prompt-operation', message_type='chat', timestamp=1),
    payload=dict(message='synthetic private prompt'))).encode()
operation = outbox.queue(prompt_wire, 1, int(time.time()*1000), int(time.time()*1000)+60000)
assert operation.phase == api.DeliveryPhase.QUEUED and operation.envelope
request = dict(event='query', operation_id=operation.operation_id, digest=list(operation.digest),
    revision=operation.revision, expires_at_ms=operation.expires_at_ms)
wire = bytes(api.application_delivery_control(json.dumps(request), 'synthetic-prompt-query'))
assert api.application_lane(wire) == 2 and json.loads(api.parse_application_delivery_control(wire)) == request
first = inbox.begin(bytes(operation.envelope), 1, int(time.time()*1000))
assert first.execute and not inbox.begin(bytes(operation.envelope), 1, int(time.time()*1000)).execute
accepted = inbox.finish(operation.operation_id, 1, int(time.time()*1000), True)
assert accepted.phase == api.DeliveryPhase.ACCEPTED
outbox.receipt(operation.operation_id, bytes(operation.digest), operation.revision, operation.expires_at_ms,
    accepted.phase, 1, int(time.time()*1000))
assert not outbox.pending(2, int(time.time()*1000))
new_revision = inbox.reset_revision(1)
assert new_revision != revision
assert inbox.query(operation.operation_id, bytes(operation.digest), revision, operation.expires_at_ms, 1,
    int(time.time()*1000)).phase == api.DeliveryPhase.DELETED
server.network_changed()
client.shutdown()
server.shutdown()
del client, server
gc.collect()
import asyncio
from shared.iroh_runtime import IrohSessionRuntime
from shared.iroh_instance import EndpointLease
from shared.session_transport import SessionBinding, TransportKind, SessionDenied
from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType

class SyntheticKeys:
    def __init__(self, name, seed):
        self.root, self.seed = Path(os.environ['AUTOYOU_TEST_ROOT'])/name, seed
    def claim_instance(self):
        return EndpointLease(self.root)
    def load(self, **kwargs):
        return bytes([self.seed])*32

async def qualify_runtime():
    contexts, enrollment, closed, files, file_services = {}, [], [], {}, {}
    async def connected(runtime, context):
        contexts[runtime.role] = context
    async def proof(runtime, context, payload):
        enrollment.append(payload)
    async def disconnected(context, binding, user_requested):
        for service in file_services.values():
            await service.closed(binding)
        closed.append((binding, user_requested))
    server_keys, client_keys = SyntheticKeys('synthetic-runtime-server', 84), SyntheticKeys('synthetic-runtime-client', 85)
    server = await IrohSessionRuntime.start(api=api, policy=json.loads(policy), keys=server_keys, role='server',
        on_connected=connected, on_enrollment=proof, on_closed=disconnected)
    client = await IrohSessionRuntime.start(api=api, policy=json.loads(policy), keys=client_keys, role='client',
        on_connected=connected, on_enrollment=proof, on_closed=disconnected)
    try:
        client.dial(server.endpoint_info.ticket, server.endpoint_info.endpoint_id)
        async def until(predicate):
            deadline = time.monotonic() + 5
            while not predicate():
                assert time.monotonic() < deadline, 'isolated Python adapter timed out'
                await asyncio.sleep(0.005)
        await until(lambda: len(contexts) == 2)
        client.send_enrollment(contexts['client'], b'synthetic enrollment fixture')
        await until(lambda: len(enrollment) == 1)
        assert enrollment == [b'synthetic enrollment fixture']
        def verified(context, device):
            return SessionBinding(endpoint_id=context.remote_endpoint_id, device_id=device,
                owner_key='synthetic-owner', canonical_user_id='synthetic-canonical-user',
                conversation_key='synthetic-conversation', transport_id=context.transport_id,
                generation=1, authorization_epoch=2, expires_at_ms=int(time.time()*1000)+60000,
                scopes=frozenset(['chat','files','browser']), transport=TransportKind.IROH)
        # This component fixture supplies the trusted authorization result.
        # The application proof/service matrix remains a separate requirement.
        server_channel = server.admit(contexts['server'], verified(contexts['server'], 'synthetic-client-device'))
        client_channel = client.admit(contexts['client'], verified(contexts['client'], 'synthetic-server-device'))
        server.activate(contexts['server'])
        client.activate(contexts['client'])
        messages = []
        async def received(message):
            messages.append(message)
        server_channel.register_handler(MessageType.CHAT, received)
        message = DataChannelMessage(MessageHeader('synthetic-runtime-chat', MessageType.CHAT, time.time(),
            'forged-session', 'forged-user'), {'text':'owned application dispatch'})
        assert await client_channel.send_message(message)
        await until(lambda: len(messages) == 1)
        assert messages[0].header.session_id == contexts['server'].transport_id
        assert messages[0].header.user_id == 'synthetic-canonical-user'
        assert message.header.session_id == 'forged-session'
        assert server_channel.is_ready
        try:
            server_channel.set_session_id('forged-rebinding')
        except SessionDenied:
            pass
        else:
            raise AssertionError('wire rebinding changed verified identity')
        from shared.iroh_body import EncryptedBody, BLOCK_BYTES
        browser_requests, browser_responses, browser_chunks = [], [], []
        async def browser_request(message):
            body = getattr(message, 'native_http_body', None)
            try:
                raw = await body.read(maximum_bytes=512*1024) if body else b''
                browser_requests.append((message.header.user_id, raw))
                await server_channel.send_message(DataChannelMessage(MessageHeader('synthetic-browser-response',
                    MessageType.HTTP_RESPONSE, time.time()), dict(request_id=message.payload['request_id'], status_code=206,
                    body='synthetic-response-🙂'*20000, headers={'Content-Type':'text/plain; charset=utf-8'},
                    raw_headers=[['Set-Cookie','a=1'],['Set-Cookie','b=2']])))
            finally:
                if body: body.close()
        async def browser_response(message):
            browser_responses.append(message)
        async def browser_chunk(message):
            browser_chunks.append(message.payload['seq'])
        server_channel.register_handler(MessageType.HTTP_REQUEST, browser_request)
        client_channel.register_handler(MessageType.HTTP_RESPONSE, browser_response)
        client_channel.register_handler(MessageType.HTTP_STREAM_DATA, browser_chunk)
        raw = bytes(range(256))*1500
        body = EncryptedBody(total=len(raw), associated_data=b'synthetic-native-upload')
        try:
            for offset in range(0,len(raw),BLOCK_BYTES): body.append(raw[offset:offset+BLOCK_BYTES])
            body.verify()
            request = DataChannelMessage(MessageHeader('synthetic-browser-request', MessageType.HTTP_REQUEST,
                time.time(), 'forged-session', 'forged-user'), dict(method='POST',url='/synthetic',headers={},
                request_id='synthetic-browser-operation',compressed=False,body_base64=False))
            assert await client_channel.send_http_request(request, body)
            await until(lambda: len(browser_responses) == 1)
        finally: body.close()
        assert browser_requests == [('synthetic-canonical-user',raw)]
        assert browser_responses[0].payload['body'] == 'synthetic-response-🙂'*20000
        assert browser_responses[0].payload['raw_headers'] == [['Set-Cookie','a=1'],['Set-Cookie','b=2']]
        for seq in range(1,41):
            assert await server_channel.send_message(DataChannelMessage(MessageHeader('synthetic-stream-chunk',
                MessageType.HTTP_STREAM_DATA,time.time()),dict(request_id='synthetic-ordering',seq=seq,data='£🙂'*1000)))
        await until(lambda: len(browser_chunks) == 40)
        assert browser_chunks == list(range(1,41))
        websocket_received = []
        async def websocket_echo(message):
            assert await server_channel.send_message(message)
        async def websocket_result(message):
            websocket_received.append(message.payload)
        server_channel.register_handler(MessageType.HTTP_WS_DATA, websocket_echo)
        client_channel.register_handler(MessageType.HTTP_WS_DATA, websocket_result)
        import base64
        websocket_bytes = bytes(range(256)) * (4*1024*1024//256)
        assert await client_channel.send_message(DataChannelMessage(MessageHeader('synthetic-large-ws',
            MessageType.HTTP_WS_DATA,time.time()),dict(request_id='synthetic-large-ws',opcode='binary',binary=True,
                data_b64=base64.b64encode(websocket_bytes).decode('ascii'))))
        await until(lambda: len(websocket_received) == 1)
        assert base64.b64decode(websocket_received[0]['data_b64'],validate=True) == websocket_bytes
        websocket_received.clear()
        websocket_text = '\x00' * (4*1024*1024)
        assert await client_channel.send_message(DataChannelMessage(MessageHeader('synthetic-large-ws-text',
            MessageType.HTTP_WS_DATA,time.time()),dict(request_id='synthetic-large-ws',data=websocket_text,opcode='text')))
        await until(lambda: len(websocket_received) == 1)
        assert websocket_received[0]['data'] == websocket_text
        slow_started, slow_release = asyncio.Event(), asyncio.Event()
        async def slow_browser(message):
            slow_started.set()
            await slow_release.wait()
        client_channel.register_handler(MessageType.HTTP_STREAM_DATA, slow_browser)
        assert await server_channel.send_message(DataChannelMessage(MessageHeader('synthetic-slow-browser',
            MessageType.HTTP_STREAM_DATA,time.time()),dict(request_id='synthetic-slow-stream',seq=1,data='slow')))
        await asyncio.wait_for(slow_started.wait(),1)
        assert await server_channel.send_ping_and_wait(timeout=1)
        slow_release.set()
        # A receipt retires the finite upload without erasing the ordered
        # browser-message stream or exhausting the logical-stream budget.
        await until(lambda: not server_channel._browser_streams and not client_channel._browser_streams)
        assert server_channel._browser_buffered_bytes == client_channel._browser_buffered_bytes == 0
        from shared.iroh_files import IrohFileService
        committed_files = []
        file_progress_started, file_progress_release = asyncio.Event(), asyncio.Event()
        async def progress(descriptor, offset, status):
            if descriptor['filename'] == 'blocked.bin' and offset == 0:
                file_progress_started.set()
                await file_progress_release.wait()
        for role, runtime, channel in [('server', server, server_channel), ('client', client, client_channel)]:
            file_services[role] = IrohFileService(root=Path(os.environ['AUTOYOU_TEST_ROOT'])/('synthetic-files-'+role),
                key=bytes([96 if role == 'server' else 97])*32,
                attachment_max_bytes=262144 if role == 'client' else 1073741824)
            await file_services[role].prepared(runtime, contexts[role], channel)
            files[role] = channel.native_files
            files[role].on_progress = progress if role == 'server' else None
            files[role].on_committed = lambda descriptor: committed_files.append(descriptor['transfer_id'])
            runtime.on_binary_frame = file_services[role].receive
        for role, channel in [('server', server_channel), ('client', client_channel)]:
            await file_services[role].ready(channel)
        file_bytes = (bytes(range(256))*600) + bytes([0,255,13,10])
        descriptor = await files['client'].stage_bytes(file_bytes, filename='native-attachment.bin', mime_type='application/octet-stream')
        receipt = await asyncio.wait_for(files['client'].send(descriptor, timeout=5), 5)
        assert receipt['phase'] == 'committed' and receipt['prefix_sha256'] == list(hashlib.sha256(file_bytes).digest())
        assert files['server'].remote_limits['attachment_max_bytes'] == 262144
        excessive = await files['server'].stage_bytes(bytes([21])*262145, filename='consumer-over-limit.bin', mime_type='application/octet-stream')
        try:
            await files['server'].send(excessive, timeout=5)
            raise AssertionError('peer consumer size bound was ignored')
        except ValueError:
            pass
        assert files['client'].incoming.checkpoint(excessive['transfer_id'], int(time.time()*1000)) is None
        restored, offset = bytearray(), 0
        while offset < len(file_bytes):
            block = await files['server'].read(descriptor['transfer_id'], offset)
            assert 0 < len(block) <= 49152
            restored.extend(block); offset += len(block)
        assert bytes(restored) == file_bytes and committed_files == [descriptor['transfer_id']]
        # A repeat uses the durable receipt, without resending bytes or notifying
        # the application twice. This is file commit dedupe, not tool replay.
        assert (await files['client'].send(descriptor, timeout=5))['phase'] == 'committed'
        assert committed_files == [descriptor['transfer_id']]
        selection = Path(os.environ['AUTOYOU_TEST_ROOT'])/'synthetic-selected-file.bin'
        selection_hash = hashlib.sha256()
        with selection.open('wb') as output:
            for _ in range(86):
                block = bytes([17])*49152
                selection_hash.update(block); output.write(block)
            selection_hash.update(bytes([0,255,13,10])); output.write(bytes([0,255,13,10]))
        selected = await files['client'].stage_path(selection, mime_type='application/octet-stream')
        assert selected['total'] > 4*1024*1024 and selected['sha256'] == list(selection_hash.digest())
        assert (await files['client'].send(selected, timeout=5))['phase'] == 'committed'
        assert bytes(await files['server'].read(selected['transfer_id'], selected['total'] - 4)) == bytes([0,255,13,10])
        blocked = await files['client'].stage_bytes(bytes([32])*400000, filename='blocked.bin', mime_type='application/octet-stream')
        sending = asyncio.create_task(files['client'].send(blocked, timeout=5))
        await asyncio.wait_for(file_progress_started.wait(), 2)
        assert await client_channel.send_ping_and_wait(timeout=1)
        await files['client'].cancel(blocked)
        file_progress_release.set()
        try:
            await sending
        except (ConnectionError, api.BindingError.FileDeleted):
            pass
        else:
            raise AssertionError('explicit file cancellation was acknowledged as committed')
        assert blocked['transfer_id'] not in committed_files
        await until(lambda: files['server'].receiver.active_count() == 0)
    finally:
        await asyncio.gather(client.close(), server.close())
    assert server._dispatch_bytes == client._dispatch_bytes == 0
    assert not server._connections and not client._connections
    assert len(closed) == 2
    with server_keys.claim_instance(), client_keys.claim_instance():
        pass
async def qualify_paired_admission():
    from shared import keystore
    from shared.iroh_keys import EndpointKeys
    from shared.iroh_state_store import ProtectedTransportState
    from shared.iroh_grants import EndpointGrantRegistry, PairedEndpoint
    from shared.iroh_admission import PairedEndpointAdmission
    from shared.iroh_pairing import VerifiedPairingRedemption, ClientPairingRedemption
    import base64
    credentials = {}
    def protected_get(service, username, **kwargs):
        assert '-test-' in service
        return credentials.get((service, username))
    def protected_save(service, username, seed):
        assert '-test-' in service
        credentials[service,username] = base64.urlsafe_b64encode(seed).decode()
        return True
    keystore.get_keyring_password = protected_get
    keystore.replace_key = protected_save
    admissions, contexts, channels, ready, replies = {}, {}, {}, set(), []
    redemptions = {}
    pair_contexts = {}
    restricted_checked = []
    async def prepared(runtime, context, channel):
        contexts[runtime.role], channels[runtime.role] = context, channel
        assert not channel.is_ready
        async def received(message):
            replies.append((runtime.role, message))
        channel.register_handler(MessageType.CHAT, received)
        message = DataChannelMessage(MessageHeader('synthetic-not-yet-ready', MessageType.CHAT, time.time()), {'text':'held'})
        assert not await channel.send_message(message)
        if runtime.role == 'server':
            # Native fault fixture: simulate an authorized peer sending app
            # bytes before the foreign host's final confirmation. Rust retains
            # them while still allowing the enrollment lane to make progress.
            runtime.endpoint.activate(context.connection_id)
            early = json.dumps(dict(header=dict(message_id='synthetic-early', message_type='chat', timestamp=1.0),
                payload=dict(text='held until confirmation'))).encode()
            runtime.endpoint.send(context.connection_id, api.TransportFrame(lane=3,
                generation=channel.binding.generation, stream_id=0, sequence=0, payload=early), None)
            count = len(replies)
            await asyncio.sleep(0.05)
            assert len(replies) == count
    async def activated(runtime, context, channel):
        assert channel.is_ready
        ready.add(runtime.role)
    async def connected(runtime, context):
        if context.protocol == 'autoyou/pair/1':
            pair_contexts[runtime.role] = context
            await redemptions[runtime.role].connected(runtime, context)
        else:
            await admissions[runtime.role].connected(runtime, context)
    async def enrollment(runtime, context, payload):
        if context.protocol == 'autoyou/pair/1':
            await redemptions[runtime.role].enrollment(runtime, context, payload)
            if runtime.role == 'server' and not redemptions['server']._pending:
                paired = admissions['server'].grants.grant_for_endpoint(context.remote_endpoint_id)
                try:
                    runtime.endpoint.admit(context.connection_id, api.SessionGrant(
                        endpoint_id=paired.endpoint_id, device_id=paired.device_id,
                        owner_id=paired.owner_key, conversation_id=paired.conversation_key,
                        generation=1, authorization_epoch=paired.authorization_epoch,
                        expires_at_ms=paired.expires_at_ms, scopes=sorted(paired.scopes)))
                except api.BindingError.PermissionDenied:
                    restricted_checked.append(True)
                else:
                    raise AssertionError('pairing ALPN became an application channel')
        else:
            await admissions[runtime.role].enrollment(runtime, context, payload)
    async def closed(context, binding, user_requested):
        for admission in admissions.values():
            await admission.closed(context, binding, user_requested)
        for redemption in redemptions.values():
            await redemption.closed(context, binding, user_requested)
    for role in ['server','client']:
        store = ProtectedTransportState(keys=EndpointKeys(role=role, purpose='grants'))
        registry = EndpointGrantRegistry(store, now_ms=lambda: int(time.time()*1000))
        admissions[role] = PairedEndpointAdmission(grants=registry, role=role,
            capabilities={'chat':True, 'media':False}, on_prepared=prepared, on_ready=activated)
        redemptions[role] = (VerifiedPairingRedemption(grants=registry, capabilities={'chat':True, 'media':False})
                            if role == 'server' else ClientPairingRedemption(grants=registry))
    server = await IrohSessionRuntime.start(api=api, policy=json.loads(policy),
        keys=SyntheticKeys('synthetic-admission-server',86), role='server',
        on_connected=connected, on_enrollment=enrollment, on_closed=closed)
    client = await IrohSessionRuntime.start(api=api, policy=json.loads(policy),
        keys=SyntheticKeys('synthetic-admission-client',87), role='client',
        on_connected=connected, on_enrollment=enrollment, on_closed=closed,
        on_dial_failed=redemptions['client'].dial_failed)
    expiry = int(time.time()*1000)+60000
    def verified_pair(endpoint, device):
        return PairedEndpoint(endpoint_id=endpoint, device_id=device, owner_key='direct:synthetic-paired-owner',
            canonical_user_id='user::direct:synthetic-paired-owner', conversation_key='session::direct:synthetic-paired-owner',
            origin_transport='direct', origin_sender_id='synthetic-paired-owner', pairing_mode='pair',
            device_ownership='shared', authorization_epoch=2, expires_at_ms=expiry, scopes=frozenset(['chat','files']))
    try:
        async def until(predicate):
            deadline = time.monotonic()+5
            while not predicate():
                assert time.monotonic() < deadline, 'isolated admission fixture timed out'
                await asyncio.sleep(0.005)
        # The synthetic fixture supplies the already verified proof result.
        # Actual Pair-ALPN possession/confirmation installs the endpoint grant;
        # this is not evidence for the OTP/CPace/account entry-point matrix.
        answer = redemptions['server'].issue_after_verified_proof(server,
            verified_pair(client.endpoint_info.endpoint_id,'synthetic-client-device'))
        for role, endpoint in [('server',client.endpoint_info.endpoint_id),('client',server.endpoint_info.endpoint_id)]:
            try:
                admissions[role].grants.grant_for_endpoint(endpoint)
            except SessionDenied:
                pass
            else:
                raise AssertionError('an invitation alone installed a grant')
        future = redemptions['client'].begin_after_verified_answer(client, answer)
        paired = await asyncio.wait_for(future, timeout=5)
        assert paired.endpoint_id == server.endpoint_info.endpoint_id
        assert not contexts and not channels and not ready and not replies
        assert not redemptions['server']._issued and not redemptions['server']._pending
        assert not redemptions['client']._expected and not redemptions['client']._pending
        assert not redemptions['client']._expiry_task
        # Checked before the client's Ready callback closes this connection.
        assert restricted_checked == [True]
        await until(lambda: not server._connections and not client._connections)
        # Use a fresh Pair connection to show that the consumed secret cannot
        # enroll a second time or remove the committed grants.
        replay = redemptions['client'].begin_after_verified_answer(client, answer)
        try:
            await asyncio.wait_for(replay, timeout=5)
        except SessionDenied:
            pass
        else:
            raise AssertionError('consumed pairing invitation was replayed')
        await until(lambda: not server._connections and not client._connections)
        assert admissions['server'].grants.grant_for_endpoint(client.endpoint_info.endpoint_id).device_id == 'synthetic-client-device'
        client.dial(server.endpoint_info.ticket, server.endpoint_info.endpoint_id)
        await until(lambda: len(ready) == 2 and len(replies) == 1)
        assert replies[0][0] == 'client' and replies[0][1].payload['text'] == 'held until confirmation'
        assert channels['server'].binding.generation == channels['client'].binding.generation == 1
        message = DataChannelMessage(MessageHeader('synthetic-admitted-chat', MessageType.CHAT, time.time(),
            'forged-session', 'forged-user'), {'text':'paired endpoint dispatch'})
        assert await channels['client'].send_message(message)
        await until(lambda: len(replies) == 2)
        assert replies[1][1].header.user_id == 'user::direct:synthetic-paired-owner'
        client.disconnect(contexts['client'])
        await until(lambda: not server._connections and not client._connections)
        ready.clear()
        client.dial(server.endpoint_info.ticket, server.endpoint_info.endpoint_id)
        await until(lambda: len(ready) == 2 and len(replies) == 3)
        assert channels['server'].binding.generation == channels['client'].binding.generation == 2
        assert not admissions['server']._pending and not admissions['client']._pending
    finally:
        await asyncio.gather(client.close(), server.close())
    assert server._dispatch_bytes == client._dispatch_bytes == 0
async def qualify_business_service():
    from types import SimpleNamespace
    from shared.iroh_grants import EndpointGrantRegistry
    from shared.iroh_state_store import ProtectedTransportState
    from shared.iroh_keys import EndpointKeys
    from shared.iroh_client import IrohClientService
    from shared.session_execution import SessionExecutionManager
    from core_server.iroh_service import IrohServerService, VerifiedPairingOrigin
    from core_server import session_dispatch
    manager = SessionExecutionManager()
    calls, tasks, received, channels = [], {}, [], {}
    engine = SimpleNamespace(datachannel_managers={}, voice_call_status_by_session={},
        voice_call_playback_by_session={}, remote_desktop_control_leases_by_session={},
        remember_device_ownership=lambda *_: None, _get_audio_manager_readiness_status=lambda _: None,
        _suspend_room_bridge_transport=lambda _: None,
        _resolve_chat_identity=manager.resolve_webrtc_identity)
    def track(session, coroutine, label, **options):
        task = asyncio.create_task(coroutine)
        tasks.setdefault(session, []).append(task)
        return task
    engine._track_session_task = track
    async def cancel(session):
        owned = tasks.pop(session, [])
        for task in owned:
            if not task.done():
                task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
    engine._cancel_session_message_tasks = cancel
    async def bootstrap(*args):
        pass
    engine._publish_webrtc_capabilities = bootstrap
    engine._prime_conversation_context_status = bootstrap
    engine._flush_pending_voice_chat_messages = bootstrap
    async def scheduler(_engine, session):
        assert manager.resolve_webrtc_identity(session).owner_key == 'direct:synthetic-service-owner'
    original_scheduler = session_dispatch.flush_pending_scheduler_notifications
    session_dispatch.flush_pending_scheduler_notifications = scheduler
    async def chat(message):
        calls.append(message)
        channel = engine.datachannel_managers[message.header.session_id]
        await channel.send_message(DataChannelMessage(MessageHeader('synthetic-business-reply',
            MessageType.CHAT, time.time()), {'text':'synthetic application reply'}))
    engine._handle_chat_message = chat
    runtime = SimpleNamespace(WEBRTC=engine, bind_transport_chat_owner=manager.bind_transport_owner,
        get_session_execution_manager=lambda: manager, _resolve_conversation_identity=lambda identity: identity,
        _build_client_session_identity_payload=lambda identity, **kwargs: {
            'canonical_user_id':identity.canonical_user_id, 'canonical_session_id':identity.canonical_session_id})
    server_grants = EndpointGrantRegistry(ProtectedTransportState(keys=EndpointKeys(
        role='server', purpose='grants')), now_ms=lambda: int(time.time()*1000))
    client_grants = EndpointGrantRegistry(ProtectedTransportState(keys=EndpointKeys(
        role='client', purpose='grants')), now_ms=lambda: int(time.time()*1000))
    service = IrohServerService(runtime=runtime, grants=server_grants,
        capabilities={'chat':True, 'media':False})
    async def prepare(client, context, channel):
        channels['client'] = channel
        async def response(message):
            received.append(message)
        channel.register_handler(MessageType.CHAT, response)
    async def ready(client, context, channel, capabilities):
        assert capabilities['chat'] is True and capabilities['media'] is False
    async def closed(context, binding, requested):
        channels.pop('client', None)
    client = IrohClientService(api=api, keys=SyntheticKeys('synthetic-service-client',89),
        grants=client_grants, policy=json.loads(policy), on_prepared=prepare, on_ready=ready, on_closed=closed)
    # When tested from the monorepo, exercise its real application enrollment
    # adapter too. A standalone Server checkout still qualifies the native
    # service without claiming unavailable desktop-source coverage.
    application = None
    client_source = Path(os.environ['PYTHONPATH']).parent/'clients'/'python'
    if client_source.is_dir():
        sys.path.insert(0, str(client_source))
        from autoyou_client import AutoYouClient
        application = AutoYouClient(server_password='synthetic-password', release_generation='iroh',
            security_mode='normal', allow_interactive_prompts=False, voice_calls_enabled=False)
        application._iroh_client = client
        application.fetch_browser_server_status = bootstrap
        original_prepare, original_ready, original_closed = prepare, ready, closed
        async def prepare(runtime, context, channel):
            await application._iroh_prepared(runtime, context, channel)
            await original_prepare(runtime, context, channel)
        async def ready(runtime, context, channel, capabilities):
            await application._iroh_ready(runtime, context, channel, capabilities)
            await original_ready(runtime, context, channel, capabilities)
        async def closed(context, binding, requested):
            await application._iroh_closed(context, binding, requested)
            await original_closed(context, binding, requested)
        client.on_prepared, client.on_ready, client.on_closed = prepare, ready, closed
    try:
        await service.start(policy=json.loads(policy), unlocked_password=None, api=api,
            keys=SyntheticKeys('synthetic-service-server',88))
        await client.start()
        answer = await service.issue_after_verified_pairing({'transport':'iroh','version':1,
            'endpoint_id':client.offer()['endpoint_id']}, origin=VerifiedPairingOrigin(
                'direct','synthetic-service-owner','pair','shared'), raw_session_id='synthetic-verified-proof')
        assert answer['transport'] == 'iroh' and 'sdp' not in answer
        if application is None:
            await asyncio.wait_for(client.pair_after_verified_answer(answer['iroh']), 5)
        else:
            offer = await application.prepare_connection_offer()
            assert offer['transport'] == 'iroh' and application.pc is application.data_channel is None
            assert 'iceServers' not in json.loads(application.build_local_pair_offer_body(offer)[0])
            assert await asyncio.wait_for(application.process_autopair_answer(json.dumps(answer)), 5)
            assert application.session_is_ready and application.pc is application.data_channel is None
            assert application.active_conversation_session_id == channels['client'].binding.conversation_key
            assert application.server_conversation_user_id == channels['client'].binding.canonical_user_id
            assert await application.check_connection(timeout=1)
        assert client.snapshot()['state'] == 'online' and client.is_ready
        from shared.iroh_client_store import ClientGrantRegistry
        from shared.iroh_state_store import ProtectedTransportStateUnavailable
        from shared.session_transport import SessionDenied
        assert isinstance(client.grants, ClientGrantRegistry)
        protected = client.grants.store.path.read_bytes()
        remote = service.endpoint.endpoint_info.endpoint_id
        first = channels['client'].binding
        for generation, scopes in ((first.generation, first.scopes),
                (first.generation+1, first.scopes | {'synthetic-unapproved-scope'})):
            try:
                client.grants.binding_for_remote_generation(remote, 'synthetic-rejected-operation',
                    generation=generation, authorization_epoch=first.authorization_epoch,
                    expires_at_ms=first.expires_at_ms, scopes=scopes)
            except SessionDenied:
                pass
            else:
                raise AssertionError('client store admitted a stale or wider generation')
            assert client.grants.store.path.read_bytes() == protected
        async def until(predicate):
            deadline = time.monotonic()+5
            while not predicate():
                assert time.monotonic() < deadline, 'isolated business adapter timed out'
                await asyncio.sleep(0.005)
        await until(lambda: 'client' in channels and channels['client'].is_ready and engine.datachannel_managers)
        await channels['client'].send_message(DataChannelMessage(MessageHeader('synthetic-business-chat',
            MessageType.CHAT,time.time(),'forged-owner','forged-user'), {'text':'synthetic application request'}))
        await until(lambda: len(received) == 1)
        assert len(calls) == 1 and calls[0].header.user_id == 'user::direct:synthetic-service-owner'
        assert calls[0].header.session_id in engine.datachannel_managers
        assert received[0].payload['text'] == 'synthetic application reply'
        first_generation = channels['client'].binding.generation
        client.network_changed()
        assert client.snapshot()['generation'] == first_generation and client.is_ready
        await client.suspend()
        await until(lambda: not service.endpoint._connections and not client.runtime._connections)
        assert client.snapshot()['state'] == 'suspended' and not client.is_ready
        await client.reconnect(service.endpoint.endpoint_info.endpoint_id, service.endpoint.endpoint_info.ticket)
        assert channels['client'].binding.generation > first_generation and client.snapshot()['state'] == 'online'
        history_generation = channels['client'].binding.generation
        async def local_history_delete():
            assert not client.runtime._connections and 'client' not in channels
            try:
                await client.reconnect(service.endpoint.endpoint_info.endpoint_id, service.endpoint.endpoint_info.ticket)
            except SessionDenied:
                pass
            else:
                raise AssertionError('a new dial crossed local history deletion')
        await client.quiesce_for_history_delete(local_history_delete)
        assert channels['client'].binding.generation > history_generation and client.snapshot()['state'] == 'online'
        async def failed_history_delete():
            raise OSError('synthetic protected cleanup failure')
        try:
            await client.quiesce_for_history_delete(failed_history_delete)
        except OSError:
            pass
        else:
            raise AssertionError('failed cleanup was reported as complete')
        assert client.snapshot()['state'] == 'suspended' and not client.runtime._connections
        await client.reconnect(service.endpoint.endpoint_info.endpoint_id, service.endpoint.endpoint_info.ticket)
        async def user_disconnect_during_history_delete():
            await client.disconnect()
        await client.quiesce_for_history_delete(user_disconnect_during_history_delete)
        assert client.snapshot()['state'] == 'user_disconnected' and not client.runtime._connections
        await client.disconnect()
        await until(lambda: not service.endpoint._connections and not client.runtime._connections)
        assert client.snapshot()['state'] == 'user_disconnected' and not client._recovery
        client.grants.store.transaction(lambda value: (dict(value, unsupported_schema_field=True), None),
            default_factory=lambda: {'schema':1,'devices':{}})
        try:
            client.grants.grant_for_endpoint(remote)
        except ProtectedTransportStateUnavailable:
            pass
        else:
            raise AssertionError('client store silently recovered corrupt authorization state')
    finally:
        await service.stop()
        if application is not None:
            await application.cleanup()
        else:
            await client.close()
        session_dispatch.flush_pending_scheduler_notifications = original_scheduler
    assert service.endpoint is None and not engine.datachannel_managers and not tasks
    assert not service.business._channels and not service.admission._pending
    assert not service.pairing._issued and not service.pairing._pending
    assert not client._contexts and not client._requests and client.runtime is None
with asyncio.Runner(loop_factory=lambda: qualification_loop) as runner:
    delivery_fixture = runpy.run_path(str(Path(os.environ['AUTOYOU_QUALIFICATION_REPO_ROOT'])/'tests/server/transport/fixtures/iroh_delivery_fixture.py'))
    runner.run(delivery_fixture['qualify_delivery'](api, root))
    runner.run(qualify_runtime())
    runner.run(qualify_paired_admission())
    runner.run(qualify_business_service())
print('generated binding binary ownership, concurrent calls and shutdown passed')
"#;
    let mut command = if cfg!(windows) {
        let mut command = Command::new("py"); command.arg("-3"); command
    } else { Command::new("python3") };
    let script_path = out.join("generated_host_qualification.py");
    fs::write(&script_path, script).unwrap();
    let result = command.arg(&script_path).current_dir(&out)
        .env("PYTHONPATH", PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap())
        .env("AUTOYOU_TEST_ROOT", &out)
        .env("AUTOYOU_QUALIFICATION_REPO_ROOT", PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap().parent().unwrap())
        .env("PYTHONDONTWRITEBYTECODE", "1").output().unwrap();
    assert!(result.status.success(), "Python binding test: {}", String::from_utf8_lossy(&result.stderr));
    assert!(String::from_utf8_lossy(&result.stdout).contains("shutdown passed"));
    // Source generation is evidence only. Native compilation/device loading
    // remains pending until the final target build and acceptance milestone.
    assert!(out.join("autoyou_client_bindings.swift").is_file());
    assert!(out.join("autoyou_client_bindingsFFI.h").is_file());
    assert!(out.join("uniffi/autoyou_client_bindings/autoyou_client_bindings.kt").is_file());
}
