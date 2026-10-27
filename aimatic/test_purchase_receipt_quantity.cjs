const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = process.env.PR_SCRIPT_PATH
    ? fs.readFileSync(process.env.PR_SCRIPT_PATH, 'utf8')
    : JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/client_script.json'), 'utf8'))
        .find(row => row.name === 'prv1').script;
const flush = async () => { for (let i = 0; i < 60; i++) await Promise.resolve(); };
function deferred() {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    return {promise, resolve};
}
function harness(options = {}) {
    const row = {doctype: 'Purchase Receipt Item', name: 'ROW', item_code: 'ITEM',
        qty: 10, custom_vendor_rate: 100, custom_gst_per: 10,
        custom_gst_formula: 'GST', ...options.row};
    const hooks = {}, writes = [], timers = new Map();
    let now = 10000, timerId = 0;
    const editor = {tagName: 'INPUT', value: '74', dataset: {}, listeners: {},
        addEventListener(name, fn) { this.listeners[name] = fn; },
        removeEventListener(name) { delete this.listeners[name]; }};
    const context = {
        console: {log() {}, warn() {}, error() {}},
        locals: {'Purchase Receipt Item': {ROW: row}},
        document: {activeElement: null}, aimatic: {},
        flt: v => Number(v) || 0, cint: v => parseInt(v) || 0, __: s => s,
        Date: class extends Date { static now() { return now; } },
        setTimeout(fn, delay) { timers.set(++timerId, {fn, at: now + Math.max(0, delay)}); return timerId; },
        clearTimeout(id) { timers.delete(id); },
    };
    const frm = {doc: {docstatus: 0, is_return: 0, ignore_pricing_rule: 1, items: [row],
        ...options.doc},
        fields_dict: {items: {grid: {wrapper: {contains: input => input === editor}}}},
        async set_value(field, value) { this.doc[field] = value; },
        refresh_field() {}, set_query() {}, remove_custom_button() {}, add_custom_button() {}};
    context.frappe = {
        ui: {form: {on(dt, events) { hooks[dt] = events; }}},
        db: {
            async get_value(dt, name) {
                if (options.lookup) return options.lookup(dt, name);
                return {message: {formula_string: 'custom_gross_total * custom_gst_per / 100'}};
            },
            async get_list() { return []; },
        },
        model: {async set_value(dt, name, field, value) {
            writes.push({field, value});
            row[field] = value;
            if (options.onWrite) await options.onWrite(field, value, row);
            const fn = hooks[dt]?.[field];
            if (fn) await fn(frm, dt, name);
        }},
        show_alert() {}, msgprint(args) { throw Error(args.message); },
        utils: {escape_html: s => s},
    };
    vm.createContext(context);
    const last = source.lastIndexOf('})();');
    assert(last > 0);
    vm.runInContext(source.slice(0, last) +
        'globalThis.calcTest = {calculateRow, calculateDocument, scheduleCalculation};\n})();', context);
    return {row, frm, hooks, writes, editor, context, timers,
        calcRow: () => context.calcTest.calculateRow(frm, row.doctype, row.name),
        calc: () => context.calcTest.calculateDocument(frm),
        save: () => hooks['Purchase Receipt'].validate(frm),
        schedule: delay => context.calcTest.scheduleCalculation(frm, delay),
        focus() { context.document.activeElement = editor; },
        blur() { context.document.activeElement = null; editor.listeners.blur?.(); },
        async advance(ms) {
            now += ms;
            for (let i = 0; i < 20; i++) {
                const due = [...timers.entries()].filter(([, timer]) => timer.at <= now);
                if (!due.length) break;
                for (const [id, timer] of due) { timers.delete(id); timer.fn(); }
                await flush();
            }
            await flush();
        },
    };
}

test('delayed formula cannot overwrite a newer manual quantity', async () => {
    const started = deferred(), response = deferred();
    const h = harness({lookup: async () => { started.resolve(); return response.promise; }});
    const pending = h.calcRow();
    await started.promise;
    h.row.qty = 74;
    response.resolve({message: {formula_string: 'custom_gross_total * custom_gst_per / 100'}});
    await pending;
    assert.equal(h.row.qty, 74);
    assert.equal(h.writes.length, 0, 'discard all outputs based on the old quantity');
});

test('typing begun during a pending formula is left untouched even before blur commits it', async () => {
    const started = deferred(), response = deferred();
    const h = harness({lookup: async () => { started.resolve(); return response.promise; }});
    const pending = h.calcRow(); await started.promise; h.focus();
    response.resolve({message: {formula_string: 'custom_gross_total * custom_gst_per / 100'}});
    await pending;
    assert.equal(h.editor.value, '74'); assert.equal(h.writes.length, 0);
});

test('editing quantity between awaited field writes stops remaining stale outputs', async () => {
    const started = deferred(), response = deferred();
    const h = harness({onWrite: async field => {
        if (field === 'custom_inventory_qty') { started.resolve(); await response.promise; }
    }});
    const pending = h.calcRow(); await started.promise;
    h.row.qty = 74; response.resolve(); await pending;
    assert.equal(h.row.qty, 74);
    assert.equal(h.row.custom_gross_total, undefined);
    await h.calcRow();
    assert.equal(h.row.custom_gross_total, 7400);
    assert.equal(h.row.custom_line_total, 8140);
});

test('manual quantity is an input and ordinary calculation does not write it', async () => {
    const h = harness({row: {qty: 74}}); await h.calc();
    assert.equal(h.row.qty, 74); assert(!h.writes.some(w => w.field === 'qty'));
    assert.equal(h.row.custom_gross_total, 7400);
    assert.equal(h.frm.doc.custom_grand_line_total, 8140);
});

test('existing scheme-only zero quantity normalization is preserved', async () => {
    const h = harness({row: {qty: 0, custom_scheme_qty: 2}}); await h.calc();
    assert.equal(h.row.qty, 2); assert.equal(h.row.custom_scheme_qty, 2);
    assert.equal(h.row.custom_inventory_qty, 2); assert.equal(h.row.custom_gross_total || 0, 0);
});

test('scheme, discount, trade offer, FED and GST arithmetic stays intact', async () => {
    const h = harness({row: {custom_scheme_qty: 2, custom_discount_per: 10,
        custom_trade_offer_total: 40, custom_fed_per: 2}}); await h.calc();
    assert.equal(h.row.qty, 10); assert.equal(h.row.custom_gross_total, 800);
    assert.equal(h.row.custom_discount_amnt, 80); assert.equal(h.row.custom_fed_amount, 16);
    assert.equal(h.row.rate, 69.6); assert.equal(h.row.custom_line_total, 776);
});

const returnSource = {qty: 10, rate: 85, custom_gross_total: 1000,
    custom_discount_amnt: 100, custom_trade_offer_total: 50,
    custom_gst_amount: 90, custom_advance_tax_amount: 10, custom_price_after_taxes: 95};
test('partial return still scales original costs and preserves its negative quantity', async () => {
    const h = harness({doc: {is_return: 1}, row: {qty: -2, purchase_receipt_item: 'ORIGINAL',
        custom_scheme_qty: 8}, lookup: async () => ({message: returnSource})});
    await h.calc();
    assert.equal(h.row.qty, -2); assert.equal(h.row.custom_scheme_qty, 0);
    assert.equal(h.row.rate, 85); assert.equal(h.row.custom_line_total, -190);
    assert.equal(h.row.custom_price_after_taxes, 95);
});

test('delayed return lookup cannot overwrite a changed return quantity', async () => {
    const started = deferred(), response = deferred();
    const h = harness({doc: {is_return: 1}, row: {qty: -2, purchase_receipt_item: 'ORIGINAL'},
        lookup: async () => { started.resolve(); return response.promise; }});
    const pending = h.calcRow(); await started.promise;
    h.row.qty = -3; response.resolve({message: returnSource}); await pending;
    assert.equal(h.row.qty, -3); assert.equal(h.writes.length, 0);
});

test('quantity event during cooldown is deferred and recalculated instead of dropped', async () => {
    const h = harness(); await h.calc();
    h.row.qty = 74; h.hooks['Purchase Receipt Item'].qty(h.frm);
    assert(h.timers.size > 0);
    await h.advance(801);
    assert.equal(h.row.custom_gross_total, 7400);
    assert.equal(h.frm.doc.custom_grand_line_total, 8140);
});

test('background calculation waits until focused quantity input is committed on blur', async () => {
    const h = harness(); h.focus(); h.schedule(100); await h.advance(100);
    assert.equal(h.writes.length, 0); assert(h.editor.listeners.blur);
    h.row.qty = 74; h.blur(); await h.advance(51);
    assert.equal(h.row.custom_gross_total, 7400);
});

test('Save awaits an old calculation and then calculates the latest committed quantity', async () => {
    const started = deferred(), response = deferred();
    const h = harness({lookup: async () => { started.resolve(); return response.promise; }});
    const background = h.calc(); await started.promise;
    h.row.qty = 74; let saved = false;
    const save = h.save().then(() => { saved = true; });
    await flush(); assert.equal(saved, false);
    response.resolve({message: {formula_string: 'custom_gross_total * custom_gst_per / 100'}});
    await Promise.all([background, save]);
    assert.equal(h.row.qty, 74); assert.equal(h.row.custom_gross_total, 7400);
    assert.equal(h.frm.doc.custom_grand_line_total, 8140);
});

test('a deleted row receives no delayed formula result', async () => {
    const started = deferred(), response = deferred();
    const h = harness({lookup: async () => { started.resolve(); return response.promise; }});
    const pending = h.calcRow(); await started.promise;
    h.frm.doc.items = []; delete h.context.locals['Purchase Receipt Item'].ROW;
    response.resolve({message: {formula_string: 'custom_gross_total * custom_gst_per / 100'}});
    await pending; assert.equal(h.writes.length, 0);
});

test('submitted Purchase Receipts remain unchanged', async () => {
    const h = harness({doc: {docstatus: 1}}); await h.calc();
    assert.equal(h.writes.length, 0); assert.equal(h.row.qty, 10);
});

test('a normal zero quantity remains zero and is not replaced by the client calculation', async () => {
    const h = harness({row: {qty: 0}}); await h.calc();
    assert.equal(h.row.qty, 0); assert(!h.writes.some(w => w.field === 'qty'));
    assert.equal(h.row.custom_line_total || 0, 0);
});

test('manual fractional quantity is not silently rounded by a calculation output', async () => {
    const h = harness({row: {qty: 7.123456}}); await h.calc();
    assert.equal(h.row.qty, 7.123456); assert(!h.writes.some(w => w.field === 'qty'));
});

test('scheme quantity greater than accepted quantity is still rejected', async () => {
    const h = harness({row: {qty: 2, custom_scheme_qty: 3}});
    await assert.rejects(h.calcRow(), /Scheme quantity cannot exceed quantity/);
});

test('invalid discount is still rejected', async () => {
    const h = harness({row: {custom_discount_per: 101}});
    await assert.rejects(h.calcRow(), /Discount must be between 0 and 100/);
});

test('changing vendor rate during a formula also discards stale derived values', async () => {
    const started = deferred(), response = deferred();
    const h = harness({lookup: async () => { started.resolve(); return response.promise; }});
    const pending = h.calcRow(); await started.promise;
    h.row.custom_vendor_rate = 120;
    response.resolve({message: {formula_string: 'custom_gross_total * custom_gst_per / 100'}});
    await pending; assert.equal(h.writes.length, 0); assert.equal(h.row.custom_vendor_rate, 120);
});
