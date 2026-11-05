const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const scriptPath = process.env.AUTOFILL_SCRIPT || path.join(__dirname, 'public/js/purchase_history_autofill.js');
const source = fs.readFileSync(scriptPath, 'utf8');

function harness(doctype, docstatus = 0) {
 const handlers = {}, timers = new Map(), requests = [], writes = [];
 let nextTimer = 0, dirty = 0;
 const child = `${doctype} Item`;
 const row = {doctype: child, name: 'row-11', item_code: 'DEW', custom_mrp: 0, custom_trade_offer: 5};
 const locals = {[child]: {[row.name]: row}};
 const frm = {doc: {doctype, name: 'PO-1', docstatus, supplier: 'Pepsi', branch: 'S1', items: [row]}, dirty() {dirty++;}};
 const frappe = {
  ui: {form: {on(dt, events) {handlers[dt] = events;}}},
  defaults: {get_user_default() {return '';}},
  call(options) {requests.push(options);},
  model: {
   set_value(dt, name, field, value) {writes.push([dt, name, field, value]); locals[dt][name][field] = value;},
   trigger() {},
  },
 };
 vm.runInNewContext(source, {frappe, locals, setTimeout(fn) {timers.set(++nextTimer, fn); return nextTimer;}, clearTimeout(id) {timers.delete(id);}});
 return {frm, row, requests, writes, get dirty() {return dirty;},
  open() {handlers[doctype].onload(frm);},
  runTimers() {const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn());},
  respond() {requests.at(-1).callback({message: {history: {DEW: {custom_mrp: 120, custom_trade_offer: 9}}, fieldtypes: {custom_mrp: 'Currency', custom_trade_offer: 'Currency'}}});},
 };
}

for (const doctype of ['Purchase Order', 'Purchase Receipt', 'Purchase Invoice']) {
 test(`${doctype}: opening submitted/cancelled document leaves saved values alone`, () => {
  for (const status of [1, 2]) {
   const h = harness(doctype, status); h.open(); h.runTimers();
   assert.equal(h.requests.length, 0); assert.equal(h.writes.length, 0);
   assert.equal(h.row.custom_mrp, 0); assert.equal(h.dirty, 0);
  }
 });
 test(`${doctype}: draft preserves PO MRP and still fills receipt/invoice MRP`, () => {
  const h = harness(doctype); h.open(); h.runTimers(); h.respond();
  assert.equal(h.row.custom_mrp, doctype === 'Purchase Order' ? 0 : 120); assert.equal(h.row.custom_trade_offer, 5); assert.equal(h.dirty, doctype === 'Purchase Order' ? 0 : 1);
 });
 test(`${doctype}: submission before debounce prevents history lookup`, () => {
  const h = harness(doctype); h.open(); h.frm.doc.docstatus = 1; h.runTimers();
  assert.equal(h.requests.length, 0); assert.equal(h.row.custom_mrp, 0);
 });
 test(`${doctype}: late history response after submission cannot dirty the document`, () => {
  const h = harness(doctype); h.open(); h.runTimers(); h.frm.doc.docstatus = 1; h.respond();
  assert.equal(h.writes.length, 0); assert.equal(h.row.custom_mrp, 0); assert.equal(h.dirty, 0);
 });
 test(`${doctype}: response for a replaced form document is ignored`, () => {
  const h = harness(doctype); h.open(); h.runTimers(); h.frm.doc = {...h.frm.doc, name: 'PO-2'}; h.respond();
  assert.equal(h.writes.length, 0); assert.equal(h.dirty, 0);
 });
 test(`${doctype}: response for an item replaced during lookup is ignored`, () => {
  const h = harness(doctype); h.open(); h.runTimers(); h.row.item_code = 'OTHER'; h.respond();
  assert.equal(h.writes.length, 0); assert.equal(h.dirty, 0);
 });
}
