const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const handlers = new Map(), events = []; let frame;
const context = {performance: {now: () => 100}, console, Event: class {constructor(type) {this.type=type}},
  CustomEvent: class {constructor(type, value) {this.type=type;this.detail=value.detail}},
  DeviceMotionEvent: class {constructor(type, value) {this.type=type;Object.assign(this,value)}},
  document: {hidden: false, addEventListener() {}}, navigator: {getGamepads: () => []},
  requestAnimationFrame: callback => {frame=callback},
  addEventListener: (type, callback) => handlers.set(type, callback), dispatchEvent: event => events.push(event)};
context.window=context; vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const sdk=context.AutoYouGame, input=sdk.input, seen=[];
input.on('gesture', event => seen.push(event));
const pointer=(phase,id,x,y) => handlers.get('pointer'+phase)({pointerId:id,pointerType:'touch',clientX:x,clientY:y,buttons:phase==='up'?0:1,pressure:0.5});
pointer('down',1,10,10);pointer('up',1,10,10);assert.equal(seen.at(-1).gesture,'tap');
pointer('down',1,10,10);pointer('move',1,140,10);pointer('up',1,140,10);assert.equal(seen.at(-1).direction,'right');
pointer('down',1,0,0);pointer('down',2,100,0);pointer('move',2,200,0);
assert.equal(seen.at(-1).gesture,'pinch');assert.equal(seen.at(-1).scale,2);
pointer('up',2,200,0);assert.equal(seen.at(-1).phase,'end');
handlers.get('keydown')({code:'KeyW',key:'w'});assert.equal(input.snapshot().keys[0],'KeyW');
handlers.get('keyup')({code:'KeyW',key:'w'});assert.equal(input.snapshot().keys.length,0);
const pad={index:0,id:'Synthetic controller',connected:true,mapping:'standard',axes:[-1,0,0,1],buttons:[{value:1,pressed:true,touched:true}]};
context.__autoYouGameInput('gamepad',{gamepads:[pad]});frame();assert.equal(input.gamepads()[0].buttons[0].pressed,true);
const motion={accelerationIncludingGravity:{x:1,y:2,z:3},acceleration:{x:0,y:0,z:0},rotationRate:{alpha:4,beta:5,gamma:6},interval:33};
context.__autoYouGameInput('motion',motion);assert.equal(input.snapshot().motion.rotationRate.beta,5);
assert.equal(events.at(-1).type,'devicemotion');
let legacy;
context.autoYouGameTilt=(...axes)=>{legacy=axes};
context.__autoYouGameInput('motion',{accelerationSign:-1,accelerationIncludingGravity:{x:-1,y:-2,z:-9.80665},interval:33});
assert.equal(input.snapshot().motion.accelerationIncludingGravity.z,9.80665);
assert.deepEqual(legacy,[-1,-2,-9.80665]);
handlers.get('blur')();assert.equal(input.gamepads().length,0);handlers.get('focus')();
context.__autoYouGameInput('reset',{});assert.equal(input.snapshot().pointers.length,0);assert.equal(input.gamepads().length,0);
console.log('PASS generic taps, swipes, drag, pinch, keyboard, controller, motion and lifecycle reset');
