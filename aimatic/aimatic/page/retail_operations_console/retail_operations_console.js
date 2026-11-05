frappe.pages["retail-operations-console"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({parent: wrapper, title: __("Retail Operations"), single_column: true});
    const api = "aimatic.retail_operations.api.";
    const esc = value => frappe.utils.escape_html(String(value ?? "—"));
    const body = $("<div class='retail-operations p-3'></div>").appendTo(page.body);
    let ctx, current, offset = 0;
    let issueOffset = 0, issueSubject = "", issueStatus = "Active", issueSearch = "", issueScope = "", issueRequest = 0;
    const branch = page.add_field({fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch", change: () => {offset=0; load();}});
    const channel = page.add_field({fieldname: "channel", label: __("Report"), fieldtype: "Select", options: "Inventory", default: "Inventory", change: () => {offset=0; load();}});
    page.set_primary_action(__("Refresh evidence"), async () => {
        if (!branch.get_value()) return;
        await frappe.xcall(api+"refresh", {branch: branch.get_value()});
        frappe.show_alert({message: __("Report queued. Reload shortly to see fresh evidence."), indicator: "blue"});
    });
    page.set_secondary_action(__("Reload"), () => load());
    page.add_menu_item(__("Placed orders"), () => frappe.set_route("List", "Retail Purchase Commitment", {branch: branch.get_value()}));
    page.add_menu_item(__("Product references"), () => frappe.set_route("List", "Retail Product Reference", {branch: branch.get_value()}));
    page.add_menu_item(__("Replenishment rules"), () => frappe.set_route("List", "Retail Replenishment Rule", {branch: branch.get_value()}));
    page.add_menu_item(__("Branch settings"), () => branch.get_value() && frappe.set_route("Form", "Branch", branch.get_value()));
    page.add_menu_item(__("Ask about this report"), () => frappe.prompt({fieldname:"question",fieldtype:"Small Text",label:__("Question"),reqd:1}, async values => {
        const answer = await frappe.xcall(api+"explain", {branch:branch.get_value(), channel:channel.get_value(), question:values.question});
        frappe.msgprint({title:__("Report explanation"),message:esc(answer.text || answer.message)});
    }));
    function table(rows, columns) {
        if (!rows?.length) return "<p class='text-muted'>No recorded findings in this section.</p>";
        return "<div class='table-responsive'><table class='table table-bordered table-sm'><thead><tr>"+columns.map(c=>"<th>"+esc(c[1])+"</th>").join("")+"</tr></thead><tbody>"+rows.map(r=>"<tr>"+columns.map(c=>"<td>"+esc(Array.isArray(r[c[0]])?r[c[0]].join("; "):r[c[0]])+"</td>").join("")+"</tr>").join("")+"</tbody></table></div>";
    }
    async function load() {
        if (!ctx || !branch.get_value() || !channel.get_value()) return;
        const selected = branch.get_value(), selectedChannel=channel.get_value();
        issueRequest++;
        if (issueScope !== selected + ":" + selectedChannel) {
            issueScope = selected + ":" + selectedChannel;
            issueOffset = 0; issueSubject = ""; issueStatus = "Active"; issueSearch = "";
        }
        body.html("<p class='text-muted'>Loading recorded evidence…</p>");
        try {
            const result = await frappe.xcall(api+"report", {branch:selected,channel:selectedChannel,offset,limit:100});
            if (selected!==branch.get_value() || selectedChannel!==channel.get_value()) return;
            current=result;
            if (result.status!=="Ready") {body.text(result.message || result.error || result.status);return;}
            const r=result.report;
            let html="<div class='alert alert-info'><b>"+esc(selected)+"</b> · Evidence: "+esc(result.cutoff)+" · Business date: "+esc(result.business_date)+"<br>"+esc(ctx.discord_enabled?"Discord sending enabled for configured branches.":"Discord sending is off. Reports remain available here.")+"</div>";
            if (selectedChannel==="Inventory") {
                html+="<p>"+Object.entries(r.summary).map(([k,v])=>esc(k)+": <b>"+esc(v)+"</b>").join(" · ")+"</p>";
                html+="<p><button class='btn btn-default btn-sm' data-show-issues>Review tracked issues ↓</button></p>";
                html+=table(r.rows,[["item_code","Item"],["item_name","Product"],["actual_qty","Recorded"],["available_qty","Qualified available"],["daily_demand","Demand/day"],["days_cover","Cover days"],["status","Status"],["flags","Review reasons"]]);
                html+="<p>Rows "+(offset+1)+"–"+Math.min(offset+100,r.total_rows)+" of "+r.total_rows+" <button class='btn btn-sm btn-default' data-page='prev'>Previous</button> <button class='btn btn-sm btn-default' data-page='next'>Next</button></p>";
                html+="<h4>Possible reference mismatches</h4>"+table(r.reference_candidates,[["items","References"],["reason","Evidence"]]);
            } else if (selectedChannel==="Purchase") {
                html+="<p>Proposed quantities are stock units. Existing verbal/WhatsApp orders must be recorded below before another order is placed. Prices are historical estimates.</p>";
                html+="<details><summary>Placed orders awaiting stock ("+(r.commitments?.length || 0)+")</summary>"+table(r.commitments,[["name","Commitment"],["supplier","Vendor"],["fulfilment_status","Receipts"],["expected_on","Expected arrival"],["external_reference","Reference"]])+"</details>";
                html+="<details><summary>ERPNext records with unreceived quantities — review ("+(r.open_orders?.length || 0)+")</summary>"+table(r.open_orders,[["purchase_order","PO"],["supplier","Vendor"],["item_code","Item"],["qty","Unreceived stock units"],["schedule_date","Recorded schedule"]])+"</details><h4>Vendor order proposals</h4>";
                r.vendors.forEach((v,i)=>{
                    html+="<details data-vendor><summary><b>"+esc(v.vendor)+"</b> · Ready "+v.ready.length+" · Review "+v.review.length+" · Estimated "+esc(v.estimated_value.toFixed(2))+"</summary>";
                    html+=table(v.ready,[["item_code","Item"],["item_name","Product"],["available_qty","Available"],["daily_demand","Demand/day"],["days_cover","Cover"],["suggested_qty","Propose"],["stock_uom","Unit"],["estimated_value","Estimate"],["last_receipt","Last receipt"]]);
                    html+="<button class='btn btn-primary btn-sm' data-order='"+i+"'>Record order placed</button> <button class='btn btn-default btn-sm' data-export='"+i+"'>Download vendor CSV</button><h5>Stock decisions needed</h5>";
                    html+=table(v.review,[["item_code","Item"],["item_name","Product"],["daily_demand","Demand/day"],["flags","Review before ordering"]])+"</details><hr>";
                });
            } else if (selectedChannel==="Sales") {
                html+="<p>Sales: "+esc(r.gross_sales)+" · Refunds: "+esc(r.total_refunds)+" ("+r.refund_count+")</p>";
                html+=table(r.counters,[["pos_profile","Counter"],["receipts","Receipts"],["refunds","Refund value"],["refund_count","Refund count"],["refund_activity_percent","Refund activity %"],["pending_closing","Pending closing"]]);
                html+="<h4>Large refunds</h4>"+table(r.large_refunds,[["name","Refund"],["pos_profile","Counter"],["amount","Amount"],["return_against","Original sale"]]);
                html+="<h4>Margins — "+esc(r.margins.status)+"</h4><p>"+esc(r.margins.note || r.margins.reason)+"</p>";
                html+="<h5>Cost / valuation review — excluded from rankings ("+esc(r.margins.exception_count ?? "—")+")</h5>"+table(r.margins.exceptions,[["item_code","Item"],["item_name","Product"],["qty","Sold units"],["sales","Sale value"],["cogs","Recorded cost"],["receipt_unit_cost","Last receipt / unit"],["receipt_date","Receipt date"],["flags","Review reasons"]]);
                for (const [kind,title] of [["negative","Negative-margin sales"],["lowest","Lowest non-negative margins — screened"],["highest","Highest margins — screened"]]) html+="<h5>"+esc(title)+"</h5>"+table(r.margins[kind],[["item_code","Item"],["item_name","Product"],["qty","Sold units"],["sales","Sale value"],["cogs","Recorded cost"],["profit","Sale-only profit"],["margin_percent","Margin %"],["return_qty","Returned units"]]);
                html+="<h5>Returns — separate from sale-only rankings</h5>"+table(r.margins.returns,[["item_code","Item"],["item_name","Product"],["return_qty","Returned units"],["refund_sales","Refund net of tax"],["cost_reversal","Recorded cost reversal"],["net_sales","Sales less returns"],["net_profit","Net recorded profit"],["flags","Review reasons"]]);
                html+="<h4>Voids</h4><p>"+esc(r.void_coverage)+"</p>"+table(r.voids,[["pos_profile","Counter"],["event_type","Type"],["events","Events"],["amount","Amount"]]);
                html+="<h5>Submitted invoice cancellations (separate)</h5>"+table(r.submitted_cancellations,[["pos_profile","Counter"],["invoices","Invoices"]]);
            } else {
                html+="<p>"+esc(r.note)+"</p>"+table(r.supplier_dues,[["supplier","Vendor"],["currency","Currency"],["bills","Bills"],["outstanding","Outstanding"],["overdue","Overdue"],["earliest_due","Earliest due"]]);
                html+="<h4>Received but unbilled</h4>"+table(r.received_unbilled,[["supplier","Vendor"],["receipts","Receipts"],["estimated_unbilled_net","Estimated unbilled net"]])+"<p>"+esc(r.cash_reconciliation)+"</p>";
            }
            html+="<section data-issue-section aria-label='Tracked issues' style='scroll-margin-top:80px'><h4>Tracked issues</h4><p class='text-muted'>One finding per item or transaction. New means awaiting review. Quantities below are recorded evidence, not a physical stock count.</p><div data-issues></div></section>";
            body.html(html);
            body.find("[data-page]").on("click",e=>{offset=Math.max(0,offset+($(e.currentTarget).data("page")==="next"?100:-100));load();});
            body.find("[data-order]").on("click",e=>order(r.vendors[$(e.currentTarget).data("order")]));
            body.find("[data-export]").on("click",e=>download(r.vendors[$(e.currentTarget).data("export")]));
            body.find("[data-show-issues]").on("click",()=>body.find("[data-issue-section]")[0].scrollIntoView({behavior:"smooth",block:"start"}));
            await loadIssues(selected, selectedChannel);
        } catch(error) {body.text("Report unavailable. "+(error.message || "Check server logs."));}
    }
    async function loadIssues(selected, selectedChannel) {
        const request = ++issueRequest, container = body.find("[data-issues]");
        const fmt = value => value == null ? "—" : new Intl.NumberFormat("en", {maximumFractionDigits:3}).format(value);
        const date = value => value ? frappe.datetime.str_to_user(String(value).slice(0,19)) : "—";
        try {
            const result = await frappe.xcall(api+"issue_queue", {
                branch:selected, channel:selectedChannel, offset:issueOffset, limit:25,
                subject:issueSubject, status:issueStatus, search:issueSearch
            });
            if (request !== issueRequest || selected !== branch.get_value() || selectedChannel !== channel.get_value()) return;
            if (issueOffset && issueOffset >= result.total) {
                issueOffset = result.total ? Math.floor((result.total-1)/25)*25 : 0;
                return loadIssues(selected, selectedChannel);
            }
            const all = result.groups.reduce((total, group)=>total+group.count,0);
            const types = [{subject:"",count:all}, ...result.groups];
            let html = `<div class="mb-3" role="group" aria-label="Issue types">${types.map((group,index)=>
                `<button class="btn btn-sm ${group.subject===issueSubject?"btn-primary":"btn-default"} mr-2 mb-2" data-issue-type="${index}" aria-pressed="${group.subject===issueSubject}">${esc(group.subject||"All types")} <b>${fmt(group.count)}</b></button>`
            ).join("")}</div>`;
            html += `<form data-issue-search class="form-inline mb-3">
                <label class="mr-2" for="retail-issue-status">Status</label>
                <select id="retail-issue-status" class="form-control input-sm mr-3" data-issue-status>${["Active","All","New","Acknowledged","Action underway","Resolved","Muted"].map(status=>`<option ${status===issueStatus?"selected":""}>${esc(status)}</option>`).join("")}</select>
                <label class="sr-only" for="retail-issue-query">Search item, code or transaction</label>
                <input id="retail-issue-query" type="search" class="form-control input-sm mr-2" placeholder="Search item, code or transaction" value="${esc(issueSearch)}" maxlength="140">
                <button class="btn btn-default btn-sm" type="submit">Search</button>
            </form>`;
            const stock = selectedChannel === "Inventory";
            const rows = result.rows;
            html += `<p class="text-muted" data-issue-count>${result.total?`${fmt(issueOffset+1)}–${fmt(issueOffset+rows.length)} of ${fmt(result.total)} matching issues`:"No issues match these filters."} · Active excludes resolved and muted findings.</p>`;
            if (rows.length) {
                html += `<div class="table-responsive"><table class="table table-bordered table-sm" data-issue-table>
                    <thead><tr>${["Product / transaction","Issue",stock?"Recorded stock":"Recorded amount / margin","Status","Last change","Investigate"].map(title=>`<th scope="col">${title}</th>`).join("")}</tr></thead><tbody>`;
                html += rows.map((issue,index)=>{
                    const identity = issue.item_name || issue.document || issue.item_code || "Details unavailable";
                    const details = [issue.item_code, issue.warehouse, issue.pos_profile].filter(Boolean).map(value=>`<div class="text-muted small">${esc(value)}</div>`).join("");
                    const quantity = stock
                        ? `<b>${fmt(issue.actual_qty)}</b> ${esc(issue.stock_uom||"")}<div class="small text-muted">Cover: ${fmt(issue.days_cover)} days · sales ${fmt(issue.daily_demand)}/day</div>`
                        : issue.amount != null ? fmt(issue.amount) : `Profit ${fmt(issue.profit)} · margin ${fmt(issue.margin_percent)}%`;
                    const flags = (Array.isArray(issue.flags)?issue.flags:[]).map(flag=>`<div class="small text-muted">${esc(flag)}</div>`).join("");
                    return `<tr><td><strong>${esc(identity)}</strong>${details}</td><td>${esc(issue.subject)}${flags}</td><td>${quantity}</td>
                        <td><span class="indicator-pill ${issue.status==="New"?"orange":"blue"}">${esc(issue.status)}</span></td>
                        <td title="Last checked: ${esc(date(issue.last_seen))}">${esc(date(issue.last_changed))}</td>
                        <td>${stock&&issue.item_code&&issue.warehouse?`<button class="btn btn-default btn-xs mb-1" data-issue-ledger="${index}">Stock ledger</button><br>`:""}<button class="btn btn-default btn-xs mb-1" data-issue-record="${index}">Details</button> <button class="btn btn-default btn-xs mb-1" data-issue-update="${index}">Update status</button></td></tr>`;
                }).join("") + "</tbody></table></div>";
            }
            html += `<div class="mb-3"><button class="btn btn-default btn-sm mr-2" data-issue-page="prev" ${issueOffset===0?"disabled":""}>Previous issues</button><button class="btn btn-default btn-sm" data-issue-page="next" ${issueOffset+rows.length>=result.total?"disabled":""}>Next issues</button></div>`;
            container.html(html);
            const reload = ()=>loadIssues(selected, selectedChannel);
            container.find("[data-issue-type]").on("click",event=>{issueSubject=types[Number(event.currentTarget.dataset.issueType)].subject;issueOffset=0;reload();});
            container.find("[data-issue-status]").on("change",event=>{issueStatus=event.currentTarget.value;issueOffset=0;reload();});
            container.find("[data-issue-search]").on("submit",event=>{event.preventDefault();issueSearch=container.find("#retail-issue-query").val().trim();issueOffset=0;reload();});
            container.find("[data-issue-page]").on("click",event=>{issueOffset=Math.max(0,issueOffset+(event.currentTarget.dataset.issuePage==="next"?25:-25));reload();});
            container.find("[data-issue-record]").on("click",event=>frappe.set_route("Form","Retail Operations Issue",rows[Number(event.currentTarget.dataset.issueRecord)].name));
            container.find("[data-issue-ledger]").on("click",event=>{
                const issue=rows[Number(event.currentTarget.dataset.issueLedger)];
                frappe.set_route("query-report","Stock Ledger",{company:result.company,item_code:issue.item_code,warehouse:issue.warehouse});
            });
            container.find("[data-issue-update]").on("click",event=>{
                const issue=rows[Number(event.currentTarget.dataset.issueUpdate)];
                frappe.prompt([{fieldname:"status",label:__("Status"),fieldtype:"Select",options:"Acknowledged\nAction underway\nResolved\nMuted",reqd:1},{fieldname:"reason",label:__("Reason"),fieldtype:"Small Text",reqd:1},{fieldname:"mute_until",label:__("Mute until"),fieldtype:"Datetime"}],async values=>{await frappe.xcall(api+"set_issue_status",{name:issue.name,...values});await reload();},__("Update issue: "+(issue.item_name||issue.item_code||issue.subject)));
            });
        } catch(error) {
            if (request===issueRequest) container.html("<p class='text-danger'>Unable to load issues. Use Reload to try again.</p>");
        }
    }
    function download(vendor) {
        const columns=["branch","item_code","item_name","stock_uom","available_qty","daily_demand","days_cover","suggested_qty","estimated_value","confidence","flags"];
        const quote=value=>'"'+String(value ?? "").replace(/^[=+@-]/,"'$&").replaceAll('"','""')+'"';
        const csv=[columns.join(","),...[...vendor.ready,...vendor.review].map(r=>columns.map(c=>quote(Array.isArray(r[c])?r[c].join("; "):r[c])).join(","))].join("\r\n");
        const a=document.createElement("a"),url=URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"}));
        a.href=url;a.download="vendor-order-proposal.csv";a.click();URL.revokeObjectURL(url);
    }
    function order(vendor) {
        if (vendor.vendor.startsWith("Transfer:") || vendor.vendor==="Unassigned source") return frappe.msgprint(__("This proposal requires a source decision before recording a vendor order."));
        const dialog=new frappe.ui.Dialog({title:__("Record order already placed"),size:"extra-large",fields:[
            {fieldname:"external_reference",label:__("Vendor/order reference"),fieldtype:"Data",reqd:1},
            {fieldname:"expected_on",label:__("Expected arrival, if known"),fieldtype:"Date"},
            {fieldname:"items",label:__("Actual ordered quantities in stock units"),fieldtype:"Table",in_place_edit:true,data:vendor.ready.map(r=>({item_code:r.item_code,qty:r.suggested_qty})),fields:[{fieldname:"item_code",label:__("Item"),fieldtype:"Link",options:"Item",in_list_view:1,reqd:1},{fieldname:"qty",label:__("Ordered quantity"),fieldtype:"Float",in_list_view:1,reqd:1}]}
        ],primary_action_label:__("Save placed order"),primary_action:async values=>{
            const name=await frappe.xcall(api+"record_order",{branch:branch.get_value(),supplier:vendor.vendor,proposal_run:current.run,...values});
            dialog.hide();frappe.set_route("Form","Retail Purchase Commitment",name);
        }});dialog.show();
    }
    frappe.xcall(api+"context").then(value=>{
        ctx=value;channel.df.options=ctx.channels.join("\n");channel.refresh();
        const requestedChannel=new URLSearchParams(window.location.search).get("channel");
        channel.set_value(ctx.channels.includes(requestedChannel)?requestedChannel:ctx.channels[0]);
        branch.get_query = ()=>({filters:{name:["in",ctx.branches.map(b=>b.name)]}});
        if (ctx.can_manage) {
            page.add_menu_item(__("Operations settings"),()=>frappe.set_route("Form","Retail Operations Settings"));
            page.add_menu_item(__("Delivery health"),()=>frappe.set_route("List","Retail Discord Delivery"));
        }
        const selected=new URLSearchParams(window.location.search).get("branch");
        branch.set_value(ctx.branches.some(b=>b.name===selected)?selected:(frappe.defaults.get_user_default("Branch") || ctx.branches[0]?.name));
        load();
    }).catch(error=>{ console.error("Operations initialization failed", error?.message || String(error)); body.text("Unable to initialize Operations Console. Reload or contact your system manager."); });
};
