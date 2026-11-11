/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

/**
 * Confirm Cash & Post saves the form before calling the method. Odoo 18's
 * FormController.onSaveError always reads error.data.message. A save that never
 * becomes an RPC error (proxy 502, worker timeout, HTML body) has no data, and
 * the client then throws "Cannot read properties of undefined (reading 'message')",
 * which hides the real failure. Re-raise those so the error service can show them.
 */
patch(FormController.prototype, {
    async onSaveError(error, options) {
        const data = error && error.data;
        if (!data || typeof data.message !== "string") {
            throw error;
        }
        return super.onSaveError(error, options);
    },
});
