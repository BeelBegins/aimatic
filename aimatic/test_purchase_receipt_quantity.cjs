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
    const hooks = {}, writes = [], timers = new Map(), logs = [];
    let now = 10000, timerId = 0;
    const editor = {tagName: 'INPUT', value: '74', dataset: {}, listeners: {},
        addEventListener(name, fn) { this.listeners[name] = fn; },
        removeEventListener(name) { delete this.listeners[name]; }};
    const context = {
        console: {log() { logs.push([...arguments]); }, warn() {}, error() {}},
        locals: {'Purchase Receipt Item': {ROW: row}},
        localStorage: {getItem: key => options.storage?.[key] ?? null},
        document: {activeElement: null}, aimatic: {},
        flt: v => Number(v) || 0, cint: v => parseInt(v) || 0, __: s => s,
        Date: class extends Date { static now() { return now; } },
        setTimeout(fn, delay) { timers.set(++timerId, {fn, at: now + Math.max(0, delay)}); return timerId; },
        clearTimeout(id) { timers.delete(id); },
    };
    const extraRows = (options.extraRows || []).map(extra => ({
        doctype: 'Purchase Receipt Item', item_code: 'ITEM2', qty: 1,
        custom_vendor_rate: 50, ...extra}));
    extraRows.forEach(extra => { context.locals['Purchase Receipt Item'][extra.name] = extra; });
    const frm = {doc: {docstatus: 0, is_return: 0, ignore_pricing_rule: 1, items: [row, ...extraRows],
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
            const target = context.locals[dt][name];
            writes.push({field, value, name});
            target[field] = value;
            if (options.onWrite) await options.onWrite(field, value, target);
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
    const rowsByName = Object.fromEntries([row, ...extraRows].map(r => [r.name, r]));
    return {row, rowsByName, frm, hooks, writes, editor, context, timers, logs,
        edit(name, field, value) {
            rowsByName[name][field] = value;
            return hooks['Purchase Receipt Item'][field](frm, 'Purchase Receipt Item', name);
        },
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

// ---- Per-row background calculation (row add/scan must not redo every row) ----
const DT = 'Purchase Receipt Item';
const staleOther = {name: 'OTHER', custom_gross_total: 1, custom_line_total: 5};

test('editing one row recalculates only that row and re-sums all totals', async () => {
    const h = harness({extraRows: [staleOther]});
    h.edit('ROW', 'qty', 20); await h.advance(100);
    assert.equal(h.row.custom_gross_total, 2000);
    assert.equal(h.rowsByName.OTHER.custom_gross_total, 1, 'unchanged row is not recalculated');
    assert(!h.writes.some(w => w.name === 'OTHER'));
    assert.equal(h.frm.doc.custom_grand_line_total, 2205);
});

test('a newly added row is calculated alone', async () => {
    const h = harness({extraRows: [staleOther]});
    h.hooks['Purchase Receipt'].items_add(h.frm, DT, 'OTHER'); await h.advance(500);
    assert.equal(h.rowsByName.OTHER.custom_gross_total, 50);
    assert(!h.writes.some(w => w.name === 'ROW'));
});

test('Save and Recalculate still recalculate every row', async () => {
    const h = harness({extraRows: [staleOther]});
    await h.save();
    assert.equal(h.row.custom_gross_total, 1000);
    assert.equal(h.rowsByName.OTHER.custom_gross_total, 50);
    assert.equal(h.frm.doc.custom_grand_line_total, 1150);
});

test('onload and supplier changes still schedule a full pass', async () => {
    const h = harness({extraRows: [staleOther]});
    h.hooks['Purchase Receipt'].supplier(h.frm); await h.advance(1200);
    assert.equal(h.row.custom_gross_total, 1000);
    assert.equal(h.rowsByName.OTHER.custom_gross_total, 50);
});

test('removing a row only re-sums totals', async () => {
    const h = harness({extraRows: [staleOther]});
    h.frm.doc.items = [h.rowsByName.OTHER];
    h.hooks['Purchase Receipt'].items_remove(h.frm); await h.advance(300);
    assert.equal(h.writes.length, 0);
    assert.equal(h.frm.doc.custom_grand_line_total, 5);
});

test("the script's own output writes do not schedule another pass", async () => {
    const h = harness({row: {custom_trade_offer_total: 40, custom_scheme_qty: 2}});
    h.edit('ROW', 'qty', 12); await h.advance(100);
    assert(h.writes.some(w => w.field === 'custom_trade_offer'));
    assert(h.writes.some(w => w.field === 'custom_scheme_qty') === false);
    assert.equal(h.timers.size, 0, 'no echo pass queued');
});

test('a user edit to another row during our write is still recalculated', async () => {
    let edited = false;
    const h = harness({extraRows: [staleOther], row: {custom_trade_offer_total: 40},
        onWrite: async (field, value, target) => {
            if (!edited && target.name === 'ROW' && field === 'custom_trade_offer') {
                edited = true; h.edit('OTHER', 'qty', 3);
            }
        }});
    h.edit('ROW', 'qty', 12); await h.advance(100); await h.advance(1000);
    assert(edited);
    assert.equal(h.rowsByName.OTHER.custom_gross_total, 150);
});

test('a row deferred by grid focus is calculated after blur', async () => {
    const h = harness({extraRows: [staleOther]});
    h.focus(); h.edit('ROW', 'qty', 30); await h.advance(100);
    assert.equal(h.writes.length, 0);
    h.blur(); await h.advance(51);
    assert.equal(h.row.custom_gross_total, 3000);
    assert.equal(h.rowsByName.OTHER.custom_gross_total, 1);
});

test('a stale row keeps itself and unprocessed rows pending for the retry', async () => {
    const started = deferred(), response = deferred();
    let first = true;
    const h = harness({extraRows: [{name: 'OTHER', custom_gst_formula: 'GST'}],
        lookup: async () => {
            if (first) { first = false; started.resolve(); return response.promise; }
            return {message: {formula_string: 'custom_gross_total * custom_gst_per / 100'}};
        }});
    h.edit('ROW', 'qty', 20); h.edit('OTHER', 'qty', 4); await h.advance(100);
    await started.promise;
    h.row.qty = 25;
    response.resolve({message: {formula_string: 'custom_gross_total * custom_gst_per / 100'}});
    await h.advance(0);
    assert.equal(h.row.custom_gross_total, undefined, 'stale outputs discarded');
    await h.advance(1200);
    assert.equal(h.row.custom_gross_total, 2500);
    assert.equal(h.rowsByName.OTHER.custom_gross_total, 200);
});

test('per-row calculation log is silent unless the debug flag is set', async () => {
    const quiet = harness(); await quiet.calc();
    assert.equal(quiet.logs.length, 0);
    const loud = harness({storage: {aimatic_pr_calc_debug: '1'}}); await loud.calc();
    assert.equal(loud.logs.length, 1);
});
