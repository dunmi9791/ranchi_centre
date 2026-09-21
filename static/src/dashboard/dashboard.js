/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { getColor } from "@web/core/colors/colors";
import { cookie } from "@web/core/browser/cookie";

/**
 * Ranchi Centre dashboard: KPI cards for unions, members, loans, savings, collections and
 * disbursements, a six-month disbursed vs collected chart and the unions with the most
 * arrears. Every figure comes from `ranchi.dashboard.get_data`, read as the current user, so
 * a credit officer only sees their own unions.
 */
export class RanchiDashboard extends Component {
    static template = "ranchi_centre.Dashboard";
    static components = { Layout };
    static props = { className: { type: String, optional: true }, "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, loading: true, error: null });
        this.canvasRef = useRef("chart");
        this.chart = null;

        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        useEffect(
            () => {
                this.renderChart();
                return () => this.destroyChart();
            },
            () => [this.state.data, this.canvasRef.el]
        );
        onWillUnmount(() => this.destroyChart());
    }

    async load() {
        this.state.loading = true;
        this.state.error = null;
        try {
            this.state.data = await this.orm.call("ranchi.dashboard", "get_data", []);
        } catch (e) {
            this.state.error = e.data?.message || e.message || String(e);
        } finally {
            this.state.loading = false;
        }
    }

    async reload() {
        await this.load();
    }

    async open(key) {
        const action = await this.orm.call("ranchi.dashboard", "open_action", [key]);
        this.action.doAction(action);
    }

    openUnion(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "ranchi.union",
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

    money(value, compact = false) {
        const n = Number(value || 0);
        let text;
        if (compact && Math.abs(n) >= 1_000_000) {
            text = (n / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 2 }) + "M";
        } else if (compact && Math.abs(n) >= 10_000) {
            text = (n / 1_000).toLocaleString(undefined, { maximumFractionDigits: 1 }) + "k";
        } else {
            text = n.toLocaleString(undefined, {
                minimumFractionDigits: 0,
                maximumFractionDigits: this.currency.decimals,
            });
        }
        const symbol = this.currency.symbol || "";
        return this.currency.position === "after" ? `${text} ${symbol}` : `${symbol}${text}`;
    }

    int(value) {
        return Number(value || 0).toLocaleString();
    }

    pct(value) {
        return `${Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
    }

    // ------------------------------------------------------------------
    // chart
    // ------------------------------------------------------------------
    destroyChart() {
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
    }

    renderChart() {
        this.destroyChart();
        const canvas = this.canvasRef.el;
        const series = this.state.data?.monthly;
        if (!canvas || !series || typeof Chart === "undefined") {
            return;
        }
        const colorScheme = cookie.get("color_scheme");
        const money = (v) => this.money(v, true);
        this.chart = new Chart(canvas, {
            type: "bar",
            data: {
                labels: series.map((m) => m.label),
                datasets: [
                    { label: "Disbursed", data: series.map((m) => m.disbursed), backgroundColor: getColor(0, colorScheme) },
                    { label: "Loan repayments", data: series.map((m) => m.repaid), backgroundColor: getColor(1, colorScheme) },
                    { label: "Savings deposits", data: series.map((m) => m.deposits), backgroundColor: getColor(2, colorScheme) },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: "bottom" },
                    tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${this.money(ctx.parsed.y)}` } },
                },
                scales: {
                    x: { grid: { display: false } },
                    y: { beginAtZero: true, ticks: { callback: money } },
                },
            },
        });
    }
}

registry.category("actions").add("ranchi_dashboard", RanchiDashboard);
