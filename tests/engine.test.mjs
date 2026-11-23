import test from 'node:test';
import assert from 'node:assert/strict';
import {checkAddress,evaluateOrders,developStrategy,testStrategy,parseAddressCSV} from '../public/engine.mjs';

test('empty addresses are retained for manual collection',()=>{
  assert.equal(checkAddress({id:'E1',address:''},'v2').status,'review');
});
test('a province/district conflict must not be automatically released',()=>{
  assert.equal(checkAddress({id:'E2',address:'四川省杭州市余杭区演示路12号'},'v2').status,'invalid');
});
test('missing house detail cannot be invented from a valid district',()=>{
  const r=checkAddress({id:'E3',address:'浙江省杭州市余杭区'},'v2');
  assert.equal(r.status,'review'); assert.equal(r.suggested,'浙江省杭州市余杭区');
});
test('a tool outage is retryable and never counted as successful verification',()=>{
  assert.equal(checkAddress({id:'E4',address:'浙江省杭州市余杭区演示路12号',toolError:true},'v2').status,'retry');
});
test('correct addresses keep their house number',()=>{
  const r=checkAddress({id:'E5',address:'浙江省杭州市余杭区演示路12号3单元602室'},'v2');
  assert.equal(r.status,'pass'); assert.equal(r.suggested,'浙江省杭州市余杭区演示路12号3单元602室');
});
test('phonetic correction without confirming evidence stays in review',()=>{
  assert.equal(checkAddress({id:'E6',address:'浙江省杭州市鱼杭区演示路12号'},'v2').status,'review');
});
test('missing gold labels do not turn into perfect quality scores',()=>{
  const m=evaluateOrders([{id:'U1',address:'a'}],[{status:'pass',suggested:'a',changed:false}],{});
  assert.equal(m.errorRate,null); assert.equal(m.labeled,0); assert.equal(m.coverage,1);
});
test('imported IDs matching object property names still remain unlabeled',()=>{
  const m=evaluateOrders([{id:'constructor',address:'a'}],[{status:'pass',suggested:'a',changed:false}],{});
  assert.equal(m.labeled,0); assert.equal(m.errorRate,null);
});
test('release error denominator is reviewed auto-pass records, not the whole batch',()=>{
  const m=evaluateOrders([{id:'a'},{id:'b'},{id:'c'}],[{status:'pass',suggested:'ok'},{status:'pass',suggested:'wrong'},{status:'review'}],{a:{auto:true,address:'ok'},b:{auto:false,address:'right'},c:{auto:false,address:''}});
  assert.equal(m.errorRate,0.5); assert.equal(m.recall,0.5); assert.equal(m.coverage,2/3);
});
test('a suggested correction without a determinable address gold is excluded from correction precision',()=>{
  const m=evaluateOrders([{id:'a'},{id:'b'}],[{status:'pass',suggested:'correct',changed:true},{status:'review',suggested:'candidate',changed:true}],{a:{auto:true,address:'correct'},b:{auto:false,address:null}});
  assert.equal(m.changed,1);assert.equal(m.correctionPrecision,1);
});
test('CSV preserves quoted commas, multiline fields and empty addresses',()=>{
  const rows=parseAddressCSV('\uFEFF订单ID,原始地址,通话证据\r\nA,"浙江省杭州市余杭区演示路12号,东门","第一句\n第二句"\r\nB,,\r\n');
  assert.equal(rows.length,2); assert.equal(rows[0]?.transcript,'第一句\n第二句'); assert.equal(rows[1]?.address,'');
});
test('duplicate import IDs are rejected instead of overwriting review records',()=>{
  assert.throws(()=>parseAddressCSV('订单ID,原始地址\nA,x\nA,y'),/重复/);
});
test('unsupported CSV schema is rejected',()=>{
  assert.throws(()=>parseAddressCSV('foo,bar\na,b'),/原始地址/);
});
const leads=[
 {id:'1',split:'train',province:'甲',operator:'移动',era:'早期',order:1},
 {id:'2',split:'train',province:'甲',operator:'移动',era:'早期',order:1},
 {id:'3',split:'train',province:'乙',operator:'联通',era:'近期',order:0},
 {id:'4',split:'train',province:'乙',operator:'联通',era:'近期',order:0},
 {id:'5',split:'validation',province:'甲',operator:'移动',era:'早期',order:1},
 {id:'6',split:'validation',province:'乙',operator:'联通',era:'近期',order:0},
 {id:'7',split:'test',province:'甲',operator:'移动',era:'早期',order:0},
 {id:'8',split:'test',province:'乙',operator:'联通',era:'近期',order:1},
];
test('selection uses training labels and never changes in response to test outcomes',()=>{
  const a=developStrategy(leads,{dimensions:2,minBase:2,minLift:1.2});
  const b=developStrategy(leads.map(r=>r.split==='test'?{...r,order:1-r.order}:r),{dimensions:2,minBase:2,minLift:1.2});
  assert.equal(a.selectedKeys?.length,1); assert.deepEqual(a.selectedKeys,b.selectedKeys); assert.equal(a.validation?.lift,2);
});
test('final evaluation applies frozen rules without picking the winning test group',()=>{
  const frozen=developStrategy(leads,{dimensions:2,minBase:2,minLift:1.2});
  const m=testStrategy(leads,frozen);
  assert.equal(m.lift,0); assert.equal(m.selected,1); assert.equal(m.total,2);
});
test('a customer occurring across splits is rejected as leakage',()=>{
  assert.throws(()=>developStrategy([...leads,{...leads[0],split:'test'}],{dimensions:2,minBase:2,minLift:1.2}),/重复|隔离/);
});
