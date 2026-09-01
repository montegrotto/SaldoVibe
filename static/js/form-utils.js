/* Amount parsing/formatting shared by every form that edits money.
 *
 * Verifikationer, bankbokföring, kund- och leverantörsfakturor each render a
 * formset of rows the user types amounts into, and each one had its own copy of
 * these three helpers — byte-identical, but five places to fix a rounding or
 * locale bug in. They live here instead.
 *
 * Amount fields accept Swedish input: '1 234,50' (non-breaking or plain spaces
 * as thousands separator, comma as decimal separator). parseAmount normalises
 * that to a JS number and yields 0 rather than NaN for junk, so callers can sum
 * without guarding every read.
 *
 * Pages destructure what they need at the top of their IIFE:
 *   const { formatAmount, parseAmountValue } = window.SaldoVibe.formUtils;
 *
 * Note: supplier_invoices/invoice_form.html also has parseDecimal, which looks
 * like parseAmountValue but uses Number() instead of parseFloat() and so
 * rejects trailing junk ('12abc' -> 0, not 12). That difference is deliberate
 * enough to leave alone — do not fold it in here without checking the callers.
 */
window.SaldoVibe = window.SaldoVibe || {};

(function (namespace) {
  'use strict';

  function formatAmount(value) {
    return value.toLocaleString('sv-SE', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function parseAmountValue(value) {
    const normalized = String(value || '').replace(/\s/g, '').replace(',', '.');
    const parsed = parseFloat(normalized);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // A debit/credit pair on one row can only hold an amount on one side, never
  // both — used by transaction_form.html and supplier_invoices/invoice_form.html,
  // which otherwise repeated the same four readonly/tabindex/class toggles per row.
  // lockedSide is 'debit', 'credit', or null/'' to unlock both.
  function setDebitCreditLock(debitInput, creditInput, lockedSide) {
    [
      [debitInput, lockedSide === 'debit'],
      [creditInput, lockedSide === 'credit'],
    ].forEach(function (pair) {
      const input = pair[0];
      const locked = pair[1];
      input.readOnly = locked;
      if (locked) {
        input.value = '0.00';
        input.tabIndex = -1;
        input.classList.add('locked-amount');
      } else {
        input.removeAttribute('tabindex');
        input.classList.remove('locked-amount');
      }
    });
  }

  // Re-fetches account balances as of a given date from the shared lookup
  // endpoint. Callers pass their own `{% url %}`-resolved endpoint since that
  // can't be baked into a static file. Resolves to null on any failure so
  // callers don't need their own try/catch.
  function fetchAccountBalances(lookupUrl, dateValue) {
    const params = new URLSearchParams({ datum: dateValue });
    return fetch(lookupUrl + '?' + params.toString())
      .then(function (resp) { return resp.ok ? resp.json() : null; })
      .catch(function () { return null; });
  }

  namespace.formUtils = {
    formatAmount: formatAmount,
    parseAmountValue: parseAmountValue,
    escapeHtml: escapeHtml,
    setDebitCreditLock: setDebitCreditLock,
    fetchAccountBalances: fetchAccountBalances,
  };
})(window.SaldoVibe);
