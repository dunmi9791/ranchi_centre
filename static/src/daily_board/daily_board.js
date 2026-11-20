/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";

const MODEL = "ranchi.daily.report.wizard";

function isoDate(d) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * Daily Collection Board: pick a date and see, for every credit officer and union, what
 * was due that day (installments, arrears brought forward, fees owed) against what was
 * collected (loan, savings, fees). Figures come from `get_board_data` on the daily report
 * wizard, the same rows as the printed Daily Collection Sheet.
 */
export class RanchiDailyBoard extends Component {
    static template = "ranchi_centre.DailyBoard";
    static components = { Layout };
    static props = { className: { type: String, optional: true }, "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            day: isoDate(new Date()),
            loanTypeId: "",
            data: null,
            loading: true,
            error: null,
            collapsed: {}, // officer key -> true
            expanded: {}, // union key -> true
            hideEmpty: false,
        });
        onWillStart(() => this.load());
    }

    // ------------------------------------------------------------------
    // data
    // ------------------------------------------------------------------
    get filters() {
        return {
            day: this.state.day,
            loan_type_ids: this.state.loanTypeId ? [Number(this.state.loanTypeId)] : [],
        };
    }

    async load() {
        this.state.loading = true;
        this.state.error = null;
        try {
            this.state.data = await this.orm.call(MODEL, "get_board_data", [], this.filters);
        } catch (e) {
            this.state.error = e.data?.message || e.message || String(e);
        } finally {
            this.state.loading = false;
        }
    }

    onDateChange(ev) {
        if (!ev.target.value) {
            return;
        }
        this.state.day = ev.target.value;
        this.load();
    }

    shiftDay(delta) {
        const [y, m, d] = this.state.day.split("-").map(Number);
        this.state.day = isoDate(new Date(y, m - 1, d + delta));
        this.load();
    }

    today() {
        this.state.day = isoDate(new Date());
        this.load();
    }

    isLoanType(id) {
        return `${id}` === this.state.loanTypeId;
    }

    onLoanTypeChange(ev) {
        this.state.loanTypeId = ev.target.value;
        this.load();
    }

    toggleEmpty() {
        this.state.hideEmpty = !this.state.hideEmpty;
    }

    async print() {
        const action = await this.orm.call(MODEL, "action_print_board", [], this.filters);
        this.action.doAction(action);
    }

    // ------------------------------------------------------------------
    // view helpers
    // ------------------------------------------------------------------
    officerKey(officer) {
        return String(officer.id || "none");
    }

    unionKey(officer, union) {
        return `${this.officerKey(officer)}-${union.id || "none"}`;
    }

    toggleOfficer(officer) {
        const key = this.officerKey(officer);
        this.state.collapsed[key] = !this.state.collapsed[key];
    }

    toggleUnion(officer, union) {
        if (!union.members.length) {
            return;
        }
        const key = this.unionKey(officer, union);
        this.state.expanded[key] = !this.state.expanded[key];
    }

    isEmpty(totals) {
        return !totals.total_due && !totals.total_collected;
    }

    visibleUnions(officer) {
        return this.state.hideEmpty ? officer.unions.filter((u) => !this.isEmpty(u.totals)) : officer.unions;
    }

    get visibleOfficers() {
        const officers = this.state.data?.officers || [];
        return this.state.hideEmpty ? officers.filter((o) => !this.isEmpty(o.totals)) : officers;
    }

    rateClass(totals) {
        const due = totals.due_today + totals.arrears_bf;
        if (!due) {
            return "text-muted";
        }
        if (totals.rate >= 100) {
            return "text-bg-success";
        }
        if (totals.rate >= 80) {
            return "text-bg-warning";
        }
        return "text-bg-danger";
    }

    openRecord(model, id) {
        if (!id) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // ------------------------------------------------------------------
    // formatting
    // ------------------------------------------------------------------
    get currency() {
        return this.state.data?.currency || { symbol: "", position: "before", decimals: 2 };
    }

    money(value) {
        const n = Number(value || 0);
        if (!n) {
            return "–";
        }
        const text = n.toLocaleString(undefined, {
            minimumFractionDigits: 0,
            maximumFractionDigits: this.currency.decimals,
        });
        const symbol = this.currency.symbol || "";
        return this.currency.position === "after" ? `${text} ${symbol}` : `${symbol}${text}`;
    }

    int(value) {
        return Number(value || 0).toLocaleString();
    }

    pct(value) {
        return `${Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
    }

    get dayLabel() {
        const d = this.state.data;
        return d ? `${d.weekday}, ${d.date}` : this.state.day;
    }

    // ------------------------------------------------------------------
    // CSV export
    // ------------------------------------------------------------------
    exportCsv() {
        const data = this.state.data;
        if (!data) {
            return;
        }
        const cols = [
            ["due_today", "Due today"],
            ["arrears_bf", "Arrears B/F"],
            ["fees_due", "Fees due"],
            ["total_due", "Total due"],
            ["collected_loan", "Loan collected"],
            ["from_savings", "Of which from savings"],
            ["collected_savings", "Savings collected"],
            ["collected_fees", "Fees collected"],
            ["total_collected", "Total collected"],
            ["shortfall", "Shortfall"],
            ["rate", "Collection rate %"],
            ["defaulters", "Defaulters"],
            ["pending", "Awaiting confirmation"],
        ];
        const esc = (v) => {
            const s = v === null || v === undefined ? "" : String(v);
            return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
        };
        const lines = [
            ["Date", "Level", "Credit officer", "Union", "Loan type", "Member", "Member No.", "Loan",
                ...cols.map((c) => c[1])].map(esc).join(","),
        ];
        const push = (level, officer, union, member, totals) => {
            lines.push([
                data.date, level, officer.name, union?.name || "", union?.loan_type || "",
                member?.name || "", member?.number || "", member?.loan || "",
                ...cols.map((c) => Math.round(Number(totals[c[0]] || 0) * 100) / 100),
            ].map(esc).join(","));
        };
        for (const officer of data.officers) {
            push("Credit officer", officer, null, null, officer.totals);
            for (const union of officer.unions) {
                push("Union", officer, union, null, union.totals);
                for (const member of union.members) {
                    push("Member", officer, union, member, member.totals);
                }
            }
        }
        push("Total", { name: "" }, null, null, data.totals);
        const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `daily-collection-board-${data.date}.csv`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    }
}

registry.category("actions").add("ranchi_daily_board", RanchiDailyBoard);
