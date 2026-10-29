# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = r"""
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const [asset, scenario] = process.argv.slice(1);
const source = readFileSync(asset, 'utf8');
const start = source.indexOf('    async function sendChatTurn()');
const end = source.indexOf('    // The owner\'s own words', start);
const nativeSetTimeout = setTimeout;
const pause = () => new Promise(resolve => nativeSetTimeout(resolve, 2));
const until = async predicate => { for (let n=0;n<500;n++) { if(predicate()) return; await pause(); } throw Error('Timed out'); };
const state = { bootstrap:{metadata:{chat_ui_protocol:1}}, chat: { userId:'synthetic-user',sessionId:'synthetic-thread',composer:'',attachments:[],messages:[],turns:[],selected:null,pendingTitle:'' } };
const input = { value:'' }, controls={innerHTML:''}, send={setAttribute(){}};
const markers = new Map();
let keyboard = false, height = '';
const workspace = { getBoundingClientRect:()=>({top:keyboard?50:160}), style:{setProperty(_k,v){height=v},removeProperty(){}} };
const root = {
    classList:{remove(){keyboard=false},toggle(_k,v){keyboard=v}},
    querySelector(selector) {
        if(selector.includes('chat-input')) return input;
        if(selector.includes('chat-turn-controls')) return controls;
        if(selector.includes('chat-send')) return send;
        if(selector.includes('chat-messages')) return { insertAdjacentHTML(){} };
        if(selector === '.ayu-chat-workspace') return workspace;
        if(selector.includes('data-chat-request-id')) { if(!markers.has(selector))markers.set(selector,{outerHTML:''});return markers.get(selector); }
        return null;
    }
};
const window = {innerWidth:390,innerHeight:844,visualViewport:{height:844,offsetTop:0,scale:1},requestAnimationFrame:()=>1};
const document={activeElement:null};
let serial=0, stopCalls=0, releaseAcceptance;
const jobs=new Map(), posts=[], notices=[];
const request = async (url,options) => {
    if(url === '/api/chat') {
        const body=JSON.parse(options.body);posts.push(body);
        jobs.set(body.request_id,{request_id:body.request_id,status:'thinking',response:null});
        if(scenario === 'acklost' && posts.length===1) throw new TypeError('Synthetic connection lost after acceptance');
        if(scenario === 'preaccept' && posts.length===1) await new Promise(resolve=>{releaseAcceptance=resolve});
        return {request_id:body.request_id,status:'queued'};
    }
    const id=url.split('/')[4];
    const job=jobs.get(id);assert.ok(job,url);
    if(url.endsWith('/stop')) {stopCalls++;job.status='stopped';}
    return {...job};
};
const api = new Function('state','root','chatDefaultUserId','chatUuid','renderApp','requestJson','postJson','button',
    'escapeHtml','chatKindForMime','chatMessageMarkup','ensureChatData','setNotice','setTimeout','clearTimeout','window','document',
    source.slice(start,end)+'\nreturn {send:sendChatTurn,stop:stopChatTurn,viewport:syncChatViewport};')(
    state,root,()=> 'synthetic-user',()=> 'turn-'+(++serial),()=>{input.value=state.chat.composer},request,async()=>({}),
    (label)=>'<button>'+label+'</button>',String,()=> 'file',()=>'<article>synthetic answer</article>',async()=>{},(kind,message)=>notices.push({kind,message}),
    (fn,ms)=>nativeSetTimeout(fn,ms<10000?0:ms),clearTimeout,window,document);
const submit=async text=>{input.value=text;await api.send();};
const complete=id=>{const job=jobs.get(id);job.status='completed';job.response={response:'answer '+id};};
if(scenario.startsWith('outdated')) {
    if(scenario==='outdated-bootstrap')state.bootstrap=null;
    else if(scenario==='outdated-metadata')state.bootstrap.metadata={};
    else state.bootstrap.metadata.chat_ui_protocol=0;
    const attachments=[{kind:'file',name:'synthetic.txt'}];
    state.chat.attachments=attachments;
    await submit('Keep this unsent draft');
    assert.equal(posts.length,0);assert.equal(state.chat.messages.length,0);assert.equal(state.chat.turns.length,0);
    assert.equal(input.value,'Keep this unsent draft');assert.equal(state.chat.composer,input.value);
    assert.equal(state.chat.attachments,attachments);
    assert.match(notices.at(-1).message,/Server restart required/);
    assert.match(notices.at(-1).message,/has not been sent/);
} else if(scenario==='viewport') {
    api.viewport();assert.equal(height,'672px');
    document.activeElement={getAttribute:()=> 'chat-input'};window.visualViewport.height=440;
    api.viewport();assert.equal(keyboard,true);assert.equal(height,'378px');
    window.visualViewport.height=844;api.viewport();assert.equal(keyboard,false);assert.equal(height,'672px');
} else {
    await submit('first message');
    await until(()=>scenario==='preaccept'?Boolean(releaseAcceptance):state.chat.turns[0].status==='thinking');
    if(scenario==='navigate') {
        const previous=state.chat.messages;
        state.chat.userId='synthetic-other-user';state.chat.sessionId='synthetic-other-thread';state.chat.messages=[];
        input.value='new draft';state.chat.composer='new draft';
        complete(posts[0].request_id);await until(()=>state.chat.turns.length===0);
        assert.equal(state.chat.messages.length,0);assert.equal(previous.length,2);assert.equal(input.value,'new draft');
    } else {
        await submit('second message');assert.equal(posts.length,1);assert.equal(state.chat.messages[1].deliveryState,'queued');
        input.value='draft while thinking';state.chat.composer=input.value;
        if(scenario==='stop'||scenario==='preaccept') {
            await api.stop();
            if(releaseAcceptance)releaseAcceptance();
        } else complete(posts[0].request_id);
        await until(()=>posts.length===2);
        assert.equal(input.value,'draft while thinking');assert.equal(posts[1].message,'second message');
        complete(posts[1].request_id);await until(()=>state.chat.turns.length===0);
        assert.equal(stopCalls,scenario==='stop'||scenario==='preaccept'?1:0);
        assert.equal(state.chat.messages.filter(m=>m.role==='assistant').length,['queue','acklost'].includes(scenario)?2:1);
        if(['stop','preaccept'].includes(scenario))assert.equal(state.chat.messages[0].deliveryState,'stopped');
    }
}
console.log('verified '+scenario);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("scenario", ["queue", "stop", "preaccept", "acklost", "navigate", "viewport",
                                      "outdated-bootstrap", "outdated-metadata", "outdated-version"])
def test_browser_chat_lifecycle_and_mobile_viewport(scenario):
    asset = Path(__file__).resolve().parents[3] / "assets/admin-ui.js"
    result = subprocess.run(["node", "--input-type=module", "-e", SCRIPT, str(asset), scenario],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


MIC_SCRIPT = r"""
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const [asset,fail] = process.argv.slice(1);
const source=readFileSync(asset,'utf8');
const start=source.indexOf('    async function assistantOpenMic(');
const end=source.indexOf('    // Speech start/end',start);
let resumes=0,stops=0,closes=0;
class Context {
    constructor(){this.state='suspended';this.sampleRate=48000;this.destination={};}
    createMediaStreamSource(){return {connect(){},disconnect(){}};}
    createScriptProcessor(){return {connect(){},disconnect(){}};}
    async resume(){resumes++;if(fail==='yes')throw Error('synthetic audio failure');this.state='running';}
    close(){closes++;return Promise.resolve();}
}
const window={AudioContext:Context,isSecureContext:true};
const navigator={mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){stops++;}}]})}};
const open=new Function('window','navigator',source.slice(start,end)+'\nreturn assistantOpenMic;')(window,navigator);
if(fail==='yes') await assert.rejects(open(()=>{}),/synthetic/);
else {const mic=await open(()=>{});mic.stop();mic.stop();}
assert.equal(resumes,1);assert.equal(stops,1);assert.equal(closes,1);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("fail", ["yes", "no"])
def test_suspended_browser_audio_is_resumed_and_resources_released(fail):
    asset = Path(__file__).resolve().parents[3] / "assets/admin-ui.js"
    result = subprocess.run(["node", "--input-type=module", "-e", MIC_SCRIPT, str(asset), fail],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


CAPTURE_SCRIPT = r"""
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source=readFileSync(process.argv[1],'utf8');
const start=source.indexOf('    async function assistantCaptureClip(');
const end=source.indexOf('    async function assistantRecordSample()',start);
let time=0, serial=0, stopped=0, resolveMic, samples;
const timers=new Map();
const advance=ms=>{time+=ms;for(const [id,t] of [...timers])if(t.at<=time){timers.delete(id);t.fn();}};
const assistant={listen:null};
const capture=new Function('assistantState','renderApp','assistantEndpointer','assistantOpenMic','assistantObserve',
    'assistantJoin','setTimeout','clearTimeout',source.slice(start,end)+'\nreturn assistantCaptureClip;')(
        ()=>assistant,()=>{},()=>({started:false,silence:0}),callback=>{samples=callback;return new Promise(resolve=>{resolveMic=resolve});},
        endpoint=>{endpoint.started=true;return true;},chunks=>new Float64Array(chunks.reduce((n,c)=>n+c.length,0)),
        (fn,ms)=>{const id=++serial;timers.set(id,{fn,at:time+ms});return id;},id=>timers.delete(id));
let finished=false;
const pending=capture('test',5).then(result=>{finished=true;return result;});
advance(7000);await Promise.resolve();assert.equal(finished,false);
resolveMic({stop(){stopped++;}});await Promise.resolve();await Promise.resolve();
samples(new Float64Array(16000));advance(7000);
assert.equal((await pending).length,16000);assert.equal(stopped,1);assert.equal(assistant.capture,null);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_recording_deadline_starts_after_microphone_permission():
    asset = Path(__file__).resolve().parents[3] / "assets/admin-ui.js"
    result = subprocess.run(["node", "--input-type=module", "-e", CAPTURE_SCRIPT, str(asset)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_zero_sensitivity_survives_browser_settings_serialization():
    asset = Path(__file__).resolve().parents[3] / "assets/admin-ui.js"
    script = r"""
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source=readFileSync(process.argv[1],'utf8');
const start=source.indexOf('    function assistantSettingsPayload(');
const end=source.indexOf('    async function assistantSaveSettings(',start);
const payload=new Function('assistantState',source.slice(start,end)+'\nreturn assistantSettingsPayload();')(
    ()=>({draft:{sensitivity:0}}));
assert.equal(payload.sensitivity,0);
"""
    result = subprocess.run(["node", "--input-type=module", "-e", script, str(asset)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
