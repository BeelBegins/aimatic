/**
 * Purchase Order / Receipt / Invoice – shared Excel-like Items grid.
 * Frozen left columns + sticky item_code + frozen action column + fullscreen.
 */

function excel_grid_debounce(fn, wait) {
    let timer = null;
    return function (...args) {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
            timer = null;
            fn.apply(this, args);
        }, wait);
    };
}

const AIMATIC_PURCHASE_EXCEL_GRID_DOCTYPES = [
    "Purchase Order",
    "Purchase Receipt",
    "Purchase Invoice",
];

function aimatic_register_purchase_excel_grid(doctype) {
    frappe.ui.form.on(doctype, {
        onload_post_render(frm) {
            setTimeout(() => setup_items_excel_grid(frm), 200);
        },

        refresh(frm) {
            setTimeout(() => setup_items_excel_grid(frm), 200);
        },
    });
}

AIMATIC_PURCHASE_EXCEL_GRID_DOCTYPES.forEach(aimatic_register_purchase_excel_grid);

function setup_items_excel_grid(frm) {
    const grid = frm.fields_dict.items?.grid;

    if (!grid || !grid.wrapper) {
        return;
    }

    inject_items_excel_css();
    inject_items_grid_overlap_fix_css();

    const $wrapper = $(grid.wrapper);
    $wrapper.addClass("excel-items-grid");

    const $customButtons = $wrapper.find(".grid-custom-buttons").first();

    // Mount beside Configure Columns / grid actions (not above the Items label).
    if (!$wrapper.find(".excel-grid-toolbar").length) {
        const $toolbar = $(`
            <div class="excel-grid-toolbar">
                <div class="excel-grid-toolbar-left">
                    <strong>Items Spreadsheet</strong>
                    <span class="excel-grid-row-count"></span>
                    <span class="excel-grid-help">Esc to close full screen</span>
                </div>

                <button
                    type="button"
                    class="btn btn-primary btn-sm excel-grid-toggle"
                    aria-pressed="false"
                >
                    ⛶ Full Screen
                </button>
            </div>
        `);

        if ($customButtons.length) {
            $customButtons.prepend($toolbar);
        } else {
            $wrapper.prepend($toolbar);
        }
    }

    update_items_grid_count(frm, $wrapper);
    mark_items_grid_sticky_columns($wrapper);
    queue_items_grid_header_sync($wrapper);

    const $toggle_button = $wrapper.find(".excel-grid-toolbar .excel-grid-toggle");

    $toggle_button
        .off("click.excelItemsGrid")
        .on("click.excelItemsGrid", function (event) {
            event.preventDefault();
            event.stopPropagation();

            const enable = !$wrapper.hasClass("excel-items-grid-fullscreen");
            set_items_grid_fullscreen($wrapper, enable);
        });

    // FIXED: Allow edit buttons to work by using stopPropagation properly
    $wrapper
        .off("change.excelItemsGrid")
        .on("change.excelItemsGrid", function () {
            requestAnimationFrame(() => {
                mark_items_grid_sticky_columns($wrapper);
                update_items_grid_count(frm, $wrapper);
            });
            
            excel_grid_debounce(() => {
                queue_items_grid_header_sync($wrapper);
            }, 180)();
        });

    // FIXED: Ensure edit buttons don't get blocked
    $wrapper
        .off("click.excelGridEditBtn")
        .on("click.excelGridEditBtn", ".btn-open-row", function (e) {
            // Let Frappe handle the grid row opening
            e.stopPropagation();
            return true;
        });

    $wrapper.find(".form-grid-container").attr("tabindex", "0");

    $(document)
        .off("keydown.excelItemsGrid")
        .on("keydown.excelItemsGrid", function (event) {
            if (
                event.key === "Escape" &&
                $(".excel-items-grid-fullscreen").length &&
                !$(".modal.show").length
            ) {
                event.preventDefault();
                const $open_grid = $(".excel-items-grid-fullscreen").first();
                set_items_grid_fullscreen($open_grid, false);
            }
        });
}

function update_items_grid_count(frm, $wrapper) {
    const row_count = Array.isArray(frm.doc.items) ? frm.doc.items.length : 0;
    $wrapper
        .find(".excel-grid-toolbar .excel-grid-row-count")
        .text(`${row_count} ${row_count === 1 ? "row" : "rows"}`);
}

function mark_items_grid_sticky_columns($wrapper) {
    $wrapper
        .find(
            ".excel-sticky-check, " +
            ".excel-sticky-index, " +
            ".excel-sticky-item, " +
            ".excel-sticky-right"
        )
        .removeClass(
            "excel-sticky-check excel-sticky-index excel-sticky-item excel-sticky-right"
        );

    $wrapper.find(".data-row > .row-check").addClass("excel-sticky-check");
    $wrapper.find(".data-row > .row-index").addClass("excel-sticky-index");
    $wrapper.find('.data-row > [data-fieldname="item_code"]').addClass("excel-sticky-item");

    // FIXED: Don't apply sticky-right to edit buttons directly
    // Instead, apply it to their parent column for better z-index handling
    $wrapper.find(".btn-open-row").each(function () {
        $(this).closest(".col").addClass("excel-sticky-right");
    });

    $wrapper.find(".grid-heading-row .data-row").each(function () {
        $(this).children(".col").last().addClass("excel-sticky-right");
    });
}

function set_items_grid_fullscreen($wrapper, enable) {
    $(".excel-items-grid-fullscreen").not($wrapper).removeClass("excel-items-grid-fullscreen");

    $wrapper.toggleClass("excel-items-grid-fullscreen", enable);
    $("body").toggleClass("excel-items-grid-body-lock", enable);

    const $button = $wrapper.find(".excel-grid-toolbar .excel-grid-toggle");

    $button
        .attr("aria-pressed", enable ? "true" : "false")
        .text(enable ? "× Close Full Screen" : "⛶ Full Screen");

    setTimeout(() => {
        mark_items_grid_sticky_columns($wrapper);
        queue_items_grid_header_sync($wrapper);

        if (enable) {
            $wrapper.find(".form-grid-container").trigger("focus");
        }
    }, 100);
}

function queue_items_grid_header_sync($wrapper) {
    const old_timer = $wrapper.data("excel-grid-header-sync-timer");
    if (old_timer) clearTimeout(old_timer);

    const timer = setTimeout(() => {
        const heading =
            $wrapper.find(".form-grid-container > .grid-heading-row").get(0) ||
            $wrapper.find(".grid-heading-row").get(0);

        if (!heading) return;

        heading.style.setProperty("height", "auto", "important");
        heading.style.setProperty("min-height", "0", "important");
        heading.style.setProperty("overflow", "visible", "important");

        requestAnimationFrame(() => {
            const visible_rows = Array.from(heading.children).filter((element) => {
                return (
                    element.classList.contains("grid-row") &&
                    element.offsetParent !== null
                );
            });

            const measured_height = visible_rows.reduce((total, element) => {
                return total + element.getBoundingClientRect().height;
            }, 0);

            const fallback_height = 46;
            const final_height = Math.max(Math.ceil(measured_height), fallback_height);

            heading.style.setProperty("height", `${final_height}px`, "important");
            heading.style.setProperty("min-height", `${final_height}px`, "important");

            $wrapper
                .get(0)
                ?.style.setProperty("--excel-heading-height", `${final_height}px`);
        });
    }, 50);

    $wrapper.data("excel-grid-header-sync-timer", timer);
}

function inject_items_excel_css() {
    if (document.getElementById("items-excel-grid-css")) {
        return;
    }

    const css = `
        body.excel-items-grid-body-lock {
            overflow: hidden !important;
        }

        .excel-items-grid {
            --excel-solid-bg: var(--card-bg, var(--fg-color, #ffffff));
            --excel-page-bg: var(--bg-color, #f5f7fa);
            --excel-border: var(--border-color, #d8dce3);
            --excel-font-size: 11px;
            --excel-label-bg: var(--gray-100, #eef1f6);
            --excel-zebra-bg: #fafbfc;
            --excel-hover-bg: #eaf3ff;
            --excel-accent: var(--primary, #2490ef);
            --excel-soft-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);

            --excel-check-width: 32px;
            --excel-index-width: 40px;
            --excel-item-width: 240px;
            --excel-action-width: 40px;
            --excel-row-height: 34px;
            --excel-grid-min-width: 1550px;

            position: relative;
            background: var(--excel-solid-bg);
            font-size: var(--excel-font-size) !important;
        }

        .excel-items-grid .data-row,
        .excel-items-grid .data-row .col,
        .excel-items-grid .data-row input,
        .excel-items-grid .data-row select,
        .excel-items-grid .data-row .static-area,
        .excel-items-grid .grid-heading-row,
        .excel-items-grid .grid-heading-row .col {
            font-size: var(--excel-font-size) !important;
            line-height: 1.25 !important;
        }

        .excel-items-grid .excel-grid-toolbar,
        .excel-items-grid > .excel-grid-toolbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            min-height: 48px;
            padding: 8px 12px;
            margin-bottom: 8px;
            position: relative;
            z-index: 100;
            background: linear-gradient(to bottom, var(--excel-solid-bg), var(--excel-label-bg, #eef1f6)) !important;
            border: 1px solid var(--excel-border);
            border-radius: 8px;
            box-shadow: var(--excel-soft-shadow);
            opacity: 1 !important;
            width: 100%;
            box-sizing: border-box;
        }

        .excel-items-grid .grid-custom-buttons .excel-grid-toolbar {
            margin-bottom: 6px;
        }

        .excel-grid-toolbar-left {
            display: flex;
            align-items: center;
            gap: 12px;
            min-width: 0;
        }

        .excel-grid-row-count {
            padding: 2px 10px;
            border-radius: 12px;
            background: var(--excel-accent, #2490ef);
            color: #fff;
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.2px;
            white-space: nowrap;
        }

        .excel-grid-help {
            color: var(--text-muted);
            font-size: 11px;
        }

        .excel-grid-toggle {
            min-width: 135px;
            pointer-events: auto !important;
            position: relative;
            z-index: 110;
            border-radius: 6px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.12);
        }

        .excel-items-grid.excel-items-grid-fullscreen {
            position: fixed !important;
            inset: 0 !important;
            width: 100vw !important;
            height: 100vh !important;
            max-width: none !important;
            margin: 0 !important;
            padding: 12px !important;
            z-index: 1040 !important;
            display: flex !important;
            flex-direction: column !important;
            background: var(--excel-page-bg) !important;
            opacity: 1 !important;
        }

        .excel-items-grid-fullscreen .excel-grid-toolbar,
        .excel-items-grid-fullscreen > .excel-grid-toolbar {
            flex: 0 0 auto;
            margin-bottom: 8px;
        }

        .excel-items-grid-fullscreen > .form-grid {
            flex: 1 1 auto;
            min-height: 0;
            display: flex;
            flex-direction: column;
            background: var(--excel-solid-bg) !important;
        }

        .excel-items-grid-fullscreen .form-grid-container {
            flex: 1 1 auto;
            min-height: 0;
            max-height: none !important;
            height: auto !important;
        }

        .excel-items-grid-fullscreen .grid-footer {
            flex: 0 0 auto;
            position: relative;
            z-index: 80;
            background: var(--excel-solid-bg) !important;
            border-top: 1px solid var(--excel-border);
            opacity: 1 !important;
        }

        .excel-items-grid .form-grid-container {
            position: relative;
            isolation: isolate;
            max-height: 68vh;
            overflow-x: auto !important;
            overflow-y: auto !important;
            overscroll-behavior: contain;
            scrollbar-gutter: stable;
            will-change: scroll-position;
            -webkit-overflow-scrolling: touch;
            background: var(--excel-solid-bg) !important;
            border: 1px solid var(--excel-border);
            border-radius: 8px;
            box-shadow: var(--excel-soft-shadow);
        }

        .excel-items-grid .form-grid-container::-webkit-scrollbar {
            width: 11px;
            height: 11px;
        }

        .excel-items-grid .form-grid-container::-webkit-scrollbar-thumb {
            background: rgba(100, 116, 139, 0.35);
            border-radius: 8px;
            border: 3px solid var(--excel-solid-bg);
        }

        .excel-items-grid .form-grid-container::-webkit-scrollbar-thumb:hover {
            background: rgba(100, 116, 139, 0.55);
        }

        .excel-items-grid .form-grid-container::-webkit-scrollbar-track {
            background: transparent;
        }

        .excel-items-grid .grid-body,
        .excel-items-grid .rows {
            overflow: visible !important;
        }

        .excel-items-grid .grid-heading-row {
            position: sticky !important;
            top: 0 !important;
            z-index: 50 !important;
            background: var(--excel-solid-bg) !important;
            opacity: 1 !important;
            border-bottom: 2px solid var(--excel-border);
        }

        .excel-items-grid .grid-heading-row,
        .excel-items-grid .grid-heading-row .data-row {
            background: var(--excel-solid-bg) !important;
            opacity: 1 !important;
        }

        .excel-items-grid .grid-heading-row .data-row,
        .excel-items-grid .grid-body .data-row {
            min-width: var(--excel-grid-min-width) !important;
            width: 100% !important;
            display: flex !important;
            flex-wrap: nowrap !important;
            margin-left: 0 !important;
            margin-right: 0 !important;
            background: var(--excel-solid-bg);
        }

        .excel-items-grid .grid-body .grid-row {
            min-width: var(--excel-grid-min-width) !important;
        }

        .excel-items-grid .grid-body .data-row {
            min-height: var(--excel-row-height);
            border-bottom: 1px solid var(--excel-border);
            transition: background-color 0.08s ease;
        }

        .excel-items-grid .grid-body .grid-row:nth-child(even) .data-row {
            background: var(--excel-zebra-bg, #fafbfc);
        }

        .excel-items-grid .grid-body .grid-row:nth-child(even) .data-row .excel-sticky-check,
        .excel-items-grid .grid-body .grid-row:nth-child(even) .data-row .excel-sticky-index,
        .excel-items-grid .grid-body .grid-row:nth-child(even) .data-row .excel-sticky-item,
        .excel-items-grid .grid-body .grid-row:nth-child(even) .data-row .excel-sticky-right {
            background: var(--excel-zebra-bg, #fafbfc) !important;
        }

        .excel-items-grid .grid-body .grid-row:hover .data-row {
            background: var(--excel-hover-bg, #eaf3ff) !important;
        }

        .excel-items-grid .data-row > .col {
            min-height: var(--excel-row-height);
            padding: 2px 6px !important;
            display: flex;
            align-items: center;
            border-right: 1px solid var(--excel-border);
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .excel-items-grid .data-row input,
        .excel-items-grid .data-row select {
            min-height: 26px;
            padding: 2px 6px !important;
        }

        .excel-items-grid .data-row > .grid-static-col:focus-within,
        .excel-items-grid .data-row > .col:focus-within {
            box-shadow: inset 0 0 0 2px var(--excel-accent, #2490ef);
            background: rgba(36, 144, 239, 0.06) !important;
            position: relative;
            z-index: 6;
        }

        .excel-items-grid .data-row > .col[data-fieldname] {
            flex: 0 0 110px !important;
            min-width: 110px !important;
        }

        .excel-items-grid .data-row > [data-fieldname="item_code"] {
            flex: 0 0 var(--excel-item-width) !important;
            width: var(--excel-item-width) !important;
            min-width: var(--excel-item-width) !important;
            max-width: var(--excel-item-width) !important;
        }

        .excel-items-grid .data-row > [data-fieldname="barcode"] {
            flex: 0 0 130px !important;
            width: 130px !important;
            min-width: 130px !important;
            max-width: 130px !important;
        }

        .excel-items-grid .data-row > [data-fieldname="item_name"] {
            flex: 0 0 260px !important;
            width: 260px !important;
            min-width: 260px !important;
            max-width: 260px !important;
        }

        .excel-items-grid .data-row > [data-fieldname="accepted_qty"],
        .excel-items-grid .data-row > [data-fieldname="received_qty"],
        .excel-items-grid .data-row > [data-fieldname="qty"] {
            flex: 0 0 100px !important;
            width: 100px !important;
            min-width: 100px !important;
            max-width: 100px !important;
        }

        .excel-items-grid .data-row > [data-fieldname="rate"] {
            flex: 0 0 115px !important;
            width: 115px !important;
            min-width: 115px !important;
            max-width: 115px !important;
        }

        .excel-items-grid .data-row > [data-fieldname="amount"] {
            flex: 0 0 125px !important;
            width: 125px !important;
            min-width: 125px !important;
            max-width: 125px !important;
        }

        /* FIXED: Sticky columns with proper z-index stacking */
        .excel-items-grid .excel-sticky-check {
            position: sticky !important;
            left: 0 !important;
            flex: 0 0 var(--excel-check-width) !important;
            width: var(--excel-check-width) !important;
            min-width: var(--excel-check-width) !important;
            z-index: 15 !important;
            justify-content: center;
            background: var(--excel-solid-bg) !important;
            opacity: 1 !important;
            pointer-events: auto !important;
        }

        .excel-items-grid .excel-sticky-index {
            position: sticky !important;
            left: var(--excel-check-width) !important;
            flex: 0 0 var(--excel-index-width) !important;
            width: var(--excel-index-width) !important;
            min-width: var(--excel-index-width) !important;
            z-index: 14 !important;
            justify-content: center;
            background: var(--excel-solid-bg) !important;
            opacity: 1 !important;
            pointer-events: auto !important;
        }

        .excel-items-grid .excel-sticky-item {
            position: sticky !important;
            left: calc(var(--excel-check-width) + var(--excel-index-width)) !important;
            z-index: 13 !important;
            background: var(--excel-solid-bg) !important;
            opacity: 1 !important;
            box-shadow: 3px 0 4px rgba(0, 0, 0, 0.10);
            pointer-events: auto !important;
        }

        /* FIXED: Right sticky column - lower z-index to not block other sticky columns */
        .excel-items-grid .excel-sticky-right {
            position: sticky !important;
            right: 0 !important;
            flex: 0 0 var(--excel-action-width) !important;
            width: var(--excel-action-width) !important;
            min-width: var(--excel-action-width) !important;
            z-index: 20 !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            background: var(--excel-solid-bg) !important;
            opacity: 1 !important;
            pointer-events: auto !important;
            cursor: pointer !important;
            box-shadow: -3px 0 4px rgba(0, 0, 0, 0.10);
        }

        .excel-items-grid .excel-sticky-right .btn-open-row {
            width: 100%;
            min-height: 40px;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            position: relative;
            z-index: 25 !important;
            pointer-events: auto !important;
            cursor: pointer !important;
            background: transparent;
            border: none;
            padding: 0;
        }

        .excel-items-grid .excel-sticky-right .btn-open-row:hover {
            background: rgba(36, 144, 239, 0.1);
            border-radius: 3px;
        }

        .excel-items-grid .excel-sticky-right .btn-open-row a,
        .excel-items-grid .excel-sticky-right > a,
        .excel-items-grid .excel-sticky-right svg {
            pointer-events: auto !important;
            opacity: 1 !important;
            filter: none !important;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        /* Header sticky cells */
        .excel-items-grid .grid-heading-row .excel-sticky-check,
        .excel-items-grid .grid-heading-row .excel-sticky-index,
        .excel-items-grid .grid-heading-row .excel-sticky-item,
        .excel-items-grid .grid-heading-row .excel-sticky-right {
            z-index: 60 !important;
            background: var(--excel-solid-bg) !important;
            opacity: 1 !important;
        }

        .excel-items-grid .grid-body .grid-row:hover .excel-sticky-check,
        .excel-items-grid .grid-body .grid-row:hover .excel-sticky-index,
        .excel-items-grid .grid-body .grid-row:hover .excel-sticky-item,
        .excel-items-grid .grid-body .grid-row:hover .excel-sticky-right {
            background: var(--excel-hover-bg, #eaf3ff) !important;
            opacity: 1 !important;
        }

        @media (max-width: 900px) {
            .excel-grid-help {
                display: none;
            }

            .excel-items-grid {
                --excel-grid-min-width: 1400px;
                --excel-item-width: 260px;
            }
        }
    `;

    $("<style>", {
        id: "items-excel-grid-css",
        text: css,
    }).appendTo("head");
}

function inject_items_grid_overlap_fix_css() {
    if (document.getElementById("items-excel-grid-overlap-fix-css")) {
        return;
    }

    const css = `
        .excel-items-grid .form-grid-container {
            position: relative !important;
            isolation: isolate !important;
            overflow-x: auto !important;
            overflow-y: auto !important;
            scroll-padding-top: var(--excel-heading-height, 46px);
        }

        .excel-items-grid .grid-heading-row {
            position: sticky !important;
            top: 0 !important;
            display: block !important;
            box-sizing: border-box !important;
            z-index: 50 !important;
            margin: 0 !important;
            padding: 0 !important;
            overflow: visible !important;
            background: var(--excel-solid-bg, #ffffff) !important;
            opacity: 1 !important;
            border-bottom: 2px solid var(--excel-border, #d8dce3) !important;
            box-shadow: 0 3px 5px rgba(0, 0, 0, 0.10);
        }

        .excel-items-grid .grid-heading-row > .grid-row {
            position: relative !important;
            top: auto !important;
            right: auto !important;
            bottom: auto !important;
            left: auto !important;
            display: block !important;
            min-width: var(--excel-grid-min-width, 1550px) !important;
            width: 100% !important;
            height: auto !important;
            min-height: 32px !important;
            margin: 0 !important;
            padding: 0 !important;
            transform: none !important;
            background: var(--excel-solid-bg, #ffffff) !important;
            opacity: 1 !important;
        }

        .excel-items-grid .grid-heading-row > .grid-row:not(.filter-row) .data-row {
            min-height: 44px !important;
            background: var(--excel-label-bg, #eef1f6) !important;
            opacity: 1 !important;
            font-weight: 600 !important;
            border-bottom: 2px solid var(--excel-border, #d8dce3) !important;
            align-items: stretch !important;
        }

        .excel-items-grid .grid-heading-row > .grid-row:not(.filter-row) .data-row > .col,
        .excel-items-grid .grid-heading-row > .grid-row:not(.filter-row) .static-area {
            background: var(--excel-label-bg, #eef1f6) !important;
            font-weight: 600 !important;
            text-transform: none !important;
        }

        .excel-items-grid .grid-heading-row > .grid-row:not(.filter-row) .data-row > .col {
            white-space: normal !important;
            overflow: visible !important;
            text-overflow: clip !important;
            display: flex !important;
            align-items: center !important;
            padding: 4px 6px !important;
            line-height: 1.2 !important;
        }

        .excel-items-grid .grid-heading-row > .grid-row:not(.filter-row) .data-row > .col .static-area,
        .excel-items-grid .grid-heading-row > .grid-row:not(.filter-row) .data-row > .col .field-area {
            white-space: normal !important;
            overflow: visible !important;
            text-overflow: clip !important;
            word-break: break-word !important;
            line-height: 1.2 !important;
        }

        .excel-items-grid .grid-heading-row > .grid-row.filter-row {
            display: none !important;
        }

        .excel-items-grid .grid-heading-row .data-row,
        .excel-items-grid .grid-heading-row .data-row > .col,
        .excel-items-grid .grid-heading-row .grid-static-col {
            background: var(--excel-solid-bg, #ffffff) !important;
            opacity: 1 !important;
        }

        .excel-items-grid .grid-body {
            position: relative !important;
            z-index: 1 !important;
            clear: both !important;
            margin-top: 0 !important;
            padding-top: 0 !important;
            overflow: visible !important;
        }

        .excel-items-grid .grid-body .rows {
            position: relative !important;
            z-index: 1 !important;
            margin-top: 0 !important;
            padding-top: 0 !important;
        }

        .excel-items-grid .excel-sticky-item {
            box-shadow: 2px 0 3px rgba(0, 0, 0, 0.08) !important;
        }

        .excel-items-grid .excel-sticky-right {
            box-shadow: -2px 0 3px rgba(0, 0, 0, 0.08) !important;
        }

        .excel-items-grid-fullscreen .form-grid-container {
            height: calc(100vh - 145px) !important;
            max-height: calc(100vh - 145px) !important;
            min-height: 0 !important;
        }
    `;

    $("<style>", {
        id: "items-excel-grid-overlap-fix-css",
        text: css,
    }).appendTo("head");
}