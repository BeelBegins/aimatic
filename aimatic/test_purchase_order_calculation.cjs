// Node tests for the Purchase Order client calculation
// ("Client Script for Tax Cal For Purchase Order" in fixtures/client_script.json).
// Run: node --test aimatic/test_purchase_order_calculation.cjs
// PO_SCRIPT_PATH=<file> tests another copy (for example the live script);
// PO_OLD_SCRIPT_PATH=<file> enables the old-vs-new arithmetic comparison.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SCRIPT_NAME = "Client Script for Tax Cal For Purchase Order";
const source = process.env.PO_SCRIPT_PATH
  ? fs.readFileSync(process.env.PO_SCRIPT_PATH, "utf8")
  : JSON.parse(
      fs.readFileSync(
        path.join(__dirname, "fixtures/client_script.json"),
        "utf8"
      )
    ).find((row) => row.name === SCRIPT_NAME).script;
const oldSource = process.env.PO_OLD_SCRIPT_PATH
  ? fs.readFileSync(process.env.PO_OLD_SCRIPT_PATH, "utf8")
  : null;

const ITEM = "Purchase Order Item";
const OUTPUTS = [
  "custom_inventory_qty",
  "custom_gross_total",
  "custom_discount_amnt",
  "custom_trade_offer_total",
  "custom_trade_offer",
  "custom_fed_amount",
  "custom_gst_amount",
  "custom_advance_tax_amount",
  "custom_price_without_taxes",
  "custom_price_after_taxes",
  "custom_line_total",
  "discount_percentage",
  "discount_amount",
  "distributed_discount_amount",
];
const TOTALS = [
  "custom_total_gross",
  "custom_total_discount",
  "custom_total_trade_offer",
  "custom_total_fed",
  "custom_total_gst",
  "custom_total_advance_tax",
  "custom_grand_line_total",
];

const flush = async () => {
  for (let i = 0; i < 80; i++) await Promise.resolve();
};
function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
const fltd = (v, d) => {
  const n = Number(v) || 0;
  return d == null ? n : Math.round(n * 10 ** d) / 10 ** d;
};

function harness(options = {}) {
  const script = options.source || source;
  const baseRows = options.rows || [{ name: "ROW" }];
  const rows = baseRows.map((extra, i) => ({
    doctype: ITEM,
    name: `R${i}`,
    item_code: "ITEM",
    qty: 10,
    custom_vendor_rate: 100,
    custom_gst_per: 10,
    custom_gst_formula: "GST",
    price_list_rate: 100,
    rate: 100,
    ...extra,
  }));
  const hooks = {},
    writes = [],
    redraws = [],
    timers = new Map(),
    logs = [],
    messages = [];
  const counts = { erpnextTotals: 0, dirty: 0 };
  let now = 10000,
    timerId = 0,
    editingRow = null;
  const editor = {
    tagName: "INPUT",
    dataset: {},
    listeners: {},
    addEventListener(name, fn) {
      this.listeners[name] = fn;
    },
    removeEventListener(name) {
      delete this.listeners[name];
    },
    closest() {
      return editingRow ? { getAttribute: () => editingRow } : null;
    },
  };
  const context = {
    console: {
      log() {
        logs.push([...arguments]);
      },
      warn() {},
      error() {},
    },
    locals: { [ITEM]: Object.fromEntries(rows.map((r) => [r.name, r])) },
    localStorage: { getItem: (key) => options.storage?.[key] ?? null },
    document: { activeElement: null },
    flt: fltd,
    cint: (v) => parseInt(v) || 0,
    __: (s) => s,
    precision: () => 2,
    Date: class extends Date {
      static now() {
        return now;
      }
    },
    setTimeout(fn, delay) {
      timers.set(++timerId, { fn, at: now + Math.max(0, delay || 0) });
      return timerId;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  };
  const frm = {
    doc: {
      docstatus: 0,
      is_return: 0,
      ignore_pricing_rule: 1,
      items: rows,
      ...options.doc,
    },
    fields_dict: {
      items: {
        grid: {
          wrapper: { contains: (input) => input === editor },
          set_value(field, value, doc) {
            redraws.push({ field, name: doc.name });
          },
        },
      },
    },
    cscript: {
      async calculate_taxes_and_totals() {
        counts.erpnextTotals++;
      },
    },
    dirty() {
      counts.dirty++;
    },
    async set_value(field, value) {
      this.doc[field] = value;
    },
    refresh_field() {},
    set_query() {},
    remove_custom_button() {},
    add_custom_button() {},
  };
  context.frappe = {
    ui: {
      form: {
        on(dt, events) {
          hooks[dt] = events;
        },
      },
    },
    db: {
      async get_value(dt, name) {
        if (options.lookup) return options.lookup(dt, name);
        return {
          message: {
            formula_string: "custom_gross_total * custom_gst_per / 100",
          },
        };
      },
      async get_list() {
        return [];
      },
    },
    model: {
      async set_value(dt, name, field, value) {
        const target = context.locals[dt][name];
        if (target[field] === value) return;
        writes.push({ field, value, name });
        target[field] = value;
        const fn = hooks[dt]?.[field];
        if (fn) await fn(frm, dt, name);
      },
    },
    show_alert() {},
    msgprint(args) {
      messages.push(args.message);
    },
    utils: { escape_html: (s) => s },
  };
  vm.createContext(context);
  const last = script.lastIndexOf("})();");
  assert(last > 0);
  vm.runInContext(
    script.slice(0, last) +
      "globalThis.calcTest = {calculateDocument, scheduleCalculation};\n})();",
    context
  );
  const byName = Object.fromEntries(rows.map((r) => [r.name, r]));
  const h = {
    rows,
    byName,
    row: rows[0],
    frm,
    hooks,
    writes,
    redraws,
    counts,
    logs,
    messages,
    editor,
    context,
    timers,
    edit(name, field, value) {
      byName[name][field] = value;
      return hooks[ITEM][field](frm, ITEM, name);
    },
    calc: () => context.calcTest.calculateDocument(frm),
    save: () => hooks["Purchase Order"].validate(frm),
    focus(rowName) {
      editingRow = rowName;
      context.document.activeElement = editor;
    },
    blur() {
      editingRow = null;
      context.document.activeElement = null;
      editor.listeners.blur?.();
    },
    async advance(ms) {
      now += ms;
      for (let i = 0; i < 50; i++) {
        const due = [...timers.entries()].filter(
          ([, timer]) => timer.at <= now
        );
        if (!due.length) break;
        for (const [id, timer] of due) {
          timers.delete(id);
          timer.fn();
        }
        await flush();
      }
      await flush();
    },
    async settle() {
      for (let i = 0; i < 10; i++) await h.advance(100);
    },
  };
  return h;
}

const CASES = [
  {},
  { qty: 74 },
  {
    custom_scheme_qty: 2,
    custom_discount_per: 10,
    custom_trade_offer_total: 40,
    custom_fed_per: 2,
  },
  {
    custom_discount_per: 1.16,
    custom_vendor_rate: 453.39,
    price_list_rate: 453.39,
    qty: 6,
  },
  {
    custom_fed_formula: "FED",
    custom_advance_tax_formula: "ADV",
    custom_advance_tax_per: 0.5,
  },
  { qty: 0, custom_scheme_qty: 2 },
  { custom_vendor_rate: 0, price_list_rate: 87.5 },
];

test(
  "row and total arithmetic matches the previous script",
  { skip: !oldSource },
  async () => {
    const lookup = async (dt, name) => ({
      message: {
        formula_string:
          {
            GST: "custom_gross_total * custom_gst_per / 100",
            FED: "(custom_gross_total - custom_discount_amnt) * 0.02",
            ADV: "(custom_gross_total + custom_gst_amount) * custom_advance_tax_per / 100",
          }[name] || "",
      },
    });
    for (const extra of CASES) {
      const before = harness({
        source: oldSource,
        rows: [extra, { qty: 3 }],
        lookup,
      });
      const after = harness({ rows: [extra, { qty: 3 }], lookup });
      await before.calc();
      await after.calc();
      for (let i = 0; i < 2; i++) {
        for (const field of [...OUTPUTS, "qty", "custom_vendor_rate"]) {
          assert.equal(
            after.rows[i][field] ?? 0,
            before.rows[i][field] ?? 0,
            `${field} for ${JSON.stringify(extra)}`
          );
        }
        assert.equal(
          after.rows[i].rate,
          fltd(before.rows[i].rate, 2),
          "rate rounded like ERPNext"
        );
      }
      for (const field of TOTALS)
        assert.equal(after.frm.doc[field], before.frm.doc[field], field);
    }
  }
);

test("calculated outputs are written without change events", async () => {
  const h = harness({ rows: [{ custom_discount_per: 10 }] });
  await h.calc();
  assert.deepEqual(
    h.writes,
    [],
    "no frappe.model.set_value for outputs or rate"
  );
  assert.equal(h.row.custom_gross_total, 1000);
  assert.equal(h.row.rate, 90);
  assert(
    h.redraws.some((r) => r.field === "rate" && r.name === "R0"),
    "changed cells redrawn"
  );
  assert.equal(h.counts.dirty, 1);
});

test("a scanned or edited row recalculates alone and totals re-sum", async () => {
  const h = harness({ rows: [{}, {}, {}] });
  await h.calc();
  h.redraws.length = 0;
  await h.edit("R1", "qty", 20);
  await h.advance(100);
  assert.deepEqual([...new Set(h.redraws.map((r) => r.name))], ["R1"]);
  assert.equal(h.byName.R1.custom_gross_total, 2000);
  assert.equal(h.frm.doc.custom_total_gross, 4000);
});

test("new row from items_add is calculated alone", async () => {
  const h = harness({ rows: [{}, {}] });
  await h.calc();
  h.redraws.length = 0;
  const row = {
    doctype: ITEM,
    name: "NEW",
    item_code: "ITEM",
    qty: 2,
    custom_vendor_rate: 50,
  };
  h.context.locals[ITEM].NEW = row;
  h.frm.doc.items.push(row);
  h.hooks["Purchase Order"].items_add(h.frm, ITEM, "NEW");
  await h.advance(500);
  assert.deepEqual([...new Set(h.redraws.map((r) => r.name))], ["NEW"]);
  assert.equal(h.frm.doc.custom_total_gross, 2100);
});

test("ERPNext totals run once per batch, after the user pauses", async () => {
  const h = harness({ rows: [{}, {}, {}] });
  await h.calc();
  await h.settle();
  h.counts.erpnextTotals = 0;
  for (const name of ["R0", "R1", "R2"]) {
    await h.edit(name, "custom_discount_per", 5);
    await h.advance(150);
  }
  await h.advance(400);
  assert.equal(
    h.counts.erpnextTotals,
    0,
    "not yet: still inside the idle window"
  );
  await h.advance(600);
  assert.equal(h.counts.erpnextTotals, 1);
});

test("ERPNext totals wait while an Items cell is being edited", async () => {
  const h = harness({ rows: [{}, {}] });
  await h.edit("R0", "custom_discount_per", 5);
  await h.advance(400);
  assert.equal(h.byName.R0.rate, 95);
  h.focus("R1");
  await h.advance(700);
  assert.equal(h.counts.erpnextTotals, 0);
  h.blur();
  await h.advance(60);
  assert.equal(h.counts.erpnextTotals, 1);
});

test("the row being typed in is left alone; other rows still calculate", async () => {
  const h = harness({ rows: [{}, {}] });
  h.focus("R0");
  h.byName.R0.qty = 7;
  h.byName.R1.qty = 3;
  h.context.calcTest.scheduleCalculation(h.frm, 100);
  await h.advance(100);
  assert.equal(
    h.byName.R0.custom_gross_total,
    undefined,
    "edited row untouched"
  );
  assert.equal(h.byName.R1.custom_gross_total, 300);
  await h.advance(1000);
  assert.equal(
    h.byName.R0.custom_gross_total,
    undefined,
    "no retry loop while typing"
  );
  assert.equal(h.timers.size, 0);
  h.blur();
  await h.advance(60);
  assert.equal(h.byName.R0.custom_gross_total, 700);
});

test("typing that starts during a pending formula discards the stale result", async () => {
  const started = deferred(),
    response = deferred();
  const h = harness({
    lookup: async () => {
      started.resolve();
      return response.promise;
    },
  });
  const pending = h.calc();
  await started.promise;
  h.row.qty = 74;
  response.resolve({
    message: { formula_string: "custom_gross_total * custom_gst_per / 100" },
  });
  await pending;
  assert.equal(h.row.custom_gross_total, undefined);
  await h.advance(300);
  assert.equal(h.row.custom_gross_total, 7400);
  assert.equal(h.frm.doc.custom_grand_line_total, 8140);
});

test("Save calculates every row, including the focused one, then ERPNext totals", async () => {
  const h = harness({ rows: [{}, {}] });
  h.focus("R0");
  h.byName.R0.qty = 4;
  await h.save();
  assert.equal(h.byName.R0.custom_gross_total, 400);
  assert.equal(h.byName.R1.custom_gross_total, 1000);
  assert.equal(h.counts.erpnextTotals, 1);
});

test("Save waits for a running background pass, then uses the latest quantity", async () => {
  const started = deferred(),
    response = deferred();
  const h = harness({
    lookup: async () => {
      started.resolve();
      return response.promise;
    },
  });
  const background = h.calc();
  await started.promise;
  h.row.qty = 74;
  let saved = false;
  const save = h.save().then(() => {
    saved = true;
  });
  await flush();
  assert.equal(saved, false);
  response.resolve({
    message: { formula_string: "custom_gross_total * custom_gst_per / 100" },
  });
  await Promise.all([background, save]);
  assert.equal(h.row.custom_gross_total, 7400);
  assert.equal(h.frm.doc.custom_grand_line_total, 8140);
});

test("ERPNext resetting rate to the price list puts the net cost back", async () => {
  const h = harness({ rows: [{ custom_discount_per: 10 }] });
  await h.calc();
  assert.equal(h.row.rate, 90);
  h.row.rate = 100;
  h.hooks[ITEM].rate(h.frm, ITEM, "R0");
  await h.advance(300);
  assert.equal(h.row.rate, 90);
  await h.settle();
  assert.deepEqual(
    h.writes,
    [],
    "restoring rate does not start a change-event loop"
  );
});

test("scheme-only zero quantity still goes through set_value for ERPNext", async () => {
  const h = harness({ rows: [{ qty: 0, custom_scheme_qty: 2 }] });
  await h.calc();
  assert.deepEqual(
    h.writes.map((w) => w.field),
    ["qty"]
  );
  assert.equal(h.row.qty, 2);
  assert.equal(h.row.custom_inventory_qty, 2);
});

test("blank vendor rate is initialized from the price list rate without re-queuing itself", async () => {
  const h = harness({
    rows: [{ custom_vendor_rate: 0, price_list_rate: 87.5 }],
  });
  await h.calc();
  assert.equal(h.row.custom_vendor_rate, 87.5);
  assert.equal(h.row.custom_gross_total, 875);
  assert(
    ![...h.timers.values()].some((t) => t.at === 10400),
    "own vendor-rate write did not queue the row again"
  );
});

test("unchanged rows do not mark the form dirty or rerun ERPNext totals", async () => {
  const h = harness({ rows: [{}, {}] });
  await h.calc();
  await h.settle();
  h.counts.dirty = 0;
  h.counts.erpnextTotals = 0;
  h.redraws.length = 0;
  await h.calc();
  await h.settle();
  assert.equal(h.counts.dirty, 0);
  assert.equal(h.counts.erpnextTotals, 0);
  assert.equal(h.redraws.length, 0);
});

test("items_remove only re-sums totals", async () => {
  const h = harness({ rows: [{}, {}] });
  await h.calc();
  h.redraws.length = 0;
  h.frm.doc.items.pop();
  h.hooks["Purchase Order"].items_remove(h.frm);
  await h.advance(300);
  assert.equal(h.redraws.length, 0);
  assert.equal(h.frm.doc.custom_total_gross, 1000);
});

test("a long order is calculated in slices so the browser can handle keystrokes", async () => {
  const h = harness({ rows: Array.from({ length: 60 }, () => ({})) });
  const pass = h.calc();
  await flush();
  const done = h.rows.filter((r) => r.custom_gross_total === 1000).length;
  assert(done > 0 && done < 60, `first slice only, got ${done}`);
  let finished = false;
  pass.then(() => {
    finished = true;
  });
  for (let i = 0; i < 200 && !finished; i++) await h.advance(0);
  assert(finished, "pass completes once the yields are released");
  assert.equal(h.rows.filter((r) => r.custom_gross_total === 1000).length, 60);
  assert.equal(h.frm.doc.custom_total_gross, 60000);
});

test("a calculation error is reported once and the other rows are still calculated", async () => {
  const h = harness({ rows: [{ custom_scheme_qty: 20 }, {}] });
  await h.calc();
  await h.settle();
  assert.equal(h.messages.length, 1);
  assert.equal(h.byName.R1.custom_gross_total, 1000);
});

test("debug log is silent unless enabled", async () => {
  const quiet = harness();
  await quiet.calc();
  assert.equal(quiet.logs.length, 0);
  const loud = harness({ storage: { aimatic_po_calc_debug: "1" } });
  await loud.calc();
  assert.equal(loud.logs.length, 1);
});
