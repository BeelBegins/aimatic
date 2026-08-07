"""Stop PO/PR custom_total_gross flicker from tax-calc client script loops."""

import re

import frappe

COOLDOWN_DECL = """    let calculationRunning = false;
    let calculationQueued = false;
    let calculationTimer = null;
    let calculationCooldownUntil = 0;
"""

OLD_DECL = """    let calculationRunning = false;
    let calculationQueued = false;
    let calculationTimer = null;
"""

NEW_REFRESH_FN = """    function refreshItemsAfterCalculation(frm) {
        /*
         * Do NOT call frm.refresh_field("items") here.
         * Remounting the Items grid after every calc re-triggers ERPNext
         * rate/amount handlers, which schedule another calc and makes
         * custom_total_gross flicker up/down. Cell values are already
         * updated via frappe.model.set_value.
         */
        return;
    }
"""


def patch_refresh_fn(script: str) -> str:
	pattern = re.compile(
		r"    function refreshItemsAfterCalculation\(frm\) \{.*?\n    \}\n\n",
		re.S,
	)
	if not pattern.search(script):
		raise RuntimeError("refreshItemsAfterCalculation not found")
	return pattern.sub(NEW_REFRESH_FN + "\n", script, count=1)


def patch_totals_setter(script: str) -> str:
	old = """        for (
            const [fieldname, value]
            of Object.entries(values)
        ) {
            if (
                different(
                    frm.doc[fieldname],
                    value
                )
            ) {
                await frm.set_value(
                    fieldname,
                    value
                );
            }
        }
    }

    // ========================================================
    // CALCULATE DOCUMENT
    // ========================================================
"""
	new = """        // Direct assignment avoids frm.set_value → dirty/refresh
        // cascades that re-enter calculateDocument and flicker totals.
        for (
            const [fieldname, value]
            of Object.entries(values)
        ) {
            if (
                different(
                    frm.doc[fieldname],
                    value
                )
            ) {
                frm.doc[fieldname] = value;
                frm.refresh_field(fieldname);
            }
        }
    }

    // ========================================================
    // CALCULATE DOCUMENT
    // ========================================================
"""
	if old not in script:
		raise RuntimeError("updateCustomTotals set_value loop not found")
	return script.replace(old, new, 1)


def patch_cooldown_finally(script: str) -> str:
	old = """        } finally {
            calculationRunning = false;

            if (calculationQueued) {
                calculationQueued = false;
                scheduleCalculation(frm, 300);
            }
        }
"""
	new = """        } finally {
            calculationRunning = false;
            // Ignore rate/refresh echoes from our own writes for a beat.
            calculationCooldownUntil = Date.now() + 800;

            if (calculationQueued) {
                calculationQueued = false;
                scheduleCalculation(frm, 300);
            }
        }
"""
	if old not in script:
		raise RuntimeError("finally block not found")
	return script.replace(old, new, 1)


def patch_schedule(script: str) -> str:
	old = """    function scheduleCalculation(
        frm,
        delay = 300
    ) {
        clearTimeout(calculationTimer);

        calculationTimer = setTimeout(
            function () {
                if (isEditingItemsGrid(frm)) {
"""
	new = """    function scheduleCalculation(
        frm,
        delay = 300
    ) {
        if (Date.now() < calculationCooldownUntil) {
            return;
        }

        clearTimeout(calculationTimer);

        calculationTimer = setTimeout(
            function () {
                if (Date.now() < calculationCooldownUntil) {
                    return;
                }
                if (isEditingItemsGrid(frm)) {
"""
	if old not in script:
		raise RuntimeError("scheduleCalculation not found")
	return script.replace(old, new, 1)


def patch_refresh_dirty(script: str) -> str:
	old = """                if (frm.is_dirty()) {
                    scheduleCalculation(frm, 600);
                }
            },
"""
	new = """                // Do not auto-recalc on dirty refresh — that loops with
                // ERPNext rate/amount updates and flickers Total Gross.
            },
"""
	if old not in script:
		raise RuntimeError("dirty refresh schedule not found")
	return script.replace(old, new, 1)


def patch_rate_handler(script: str) -> str:
	old = """            rate(frm) {
                if (!calculationRunning) {
                    scheduleCalculation(frm, 300);
                }
            }
"""
	new = """            rate(frm) {
                // rate is an OUTPUT of calculateRow (net inventory unit cost).
                // Never re-enter calculation from ERPNext/our own rate writes —
                // that is what made custom_total_gross bounce.
            }
"""
	if old not in script:
		raise RuntimeError("rate handler not found")
	return script.replace(old, new, 1)


def apply_all(script: str) -> str:
	if "calculationCooldownUntil" in script:
		return script  # already patched
	# prv1 (and some Desk exports) use CRLF; normalize for matching.
	nl = "\r\n" if "\r\n" in script else "\n"
	work = script.replace("\r\n", "\n") if nl == "\r\n" else script

	if OLD_DECL not in work:
		raise RuntimeError("decl block not found")
	work = work.replace(OLD_DECL, COOLDOWN_DECL, 1)
	work = patch_refresh_fn(work)
	work = patch_totals_setter(work)
	work = patch_cooldown_finally(work)
	work = patch_schedule(work)
	work = patch_refresh_dirty(work)
	work = patch_rate_handler(work)

	if nl == "\r\n":
		work = work.replace("\n", "\r\n")
	return work


def execute():
	targets = [
		"Client Script for Tax Cal For Purchase Order",
		"prv1",
	]
	for name in targets:
		doc = frappe.get_doc("Client Script", name)
		before = doc.script or ""
		after = apply_all(before)
		if after == before:
			print(name, "already patched or unchanged")
			continue
		doc.script = after
		doc.save(ignore_permissions=True)
		print(name, "patched", len(before), "->", len(after))
	frappe.clear_cache()
