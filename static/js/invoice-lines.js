/* The invoice line-item formset, shared by kundfakturor and återkommande fakturor.
 *
 * Both forms edit the same `lines-` formset with the same row markup: add/clone a
 * row, move it up/down, delete it, renumber the Django formset indices, pull unit
 * price and VAT from the picked article, and recompute the per-row totals. All of
 * that was duplicated byte-for-byte across the two templates.
 *
 * The only real difference is the summary panel: the one-off invoice renders
 * ex-VAT/VAT/total footers that have to be re-added after every row change, and the
 * recurring form has none. That is what `onTotalsChanged` is for — everything else
 * is identical, so it lives here.
 *
 * Usage (the page keeps its own summary/date logic):
 *   const lines = window.SaldoVibe.invoiceLines.create({
 *     wrapper, totalForms, articleUnitPriceMap, articleVatRateMap, articleMetaMap,
 *     onTotalsChanged: recalcSummaryTotals,   // omit when there is no summary
 *   });
 *   lines.renumberRows();
 *
 * The row/button selectors and the `lines-` formset prefix are hard-coded because
 * both templates render the same markup; if a third form ever reuses this, promote
 * them to config rather than forking the file.
 */
window.SaldoVibe = window.SaldoVibe || {};

(function (namespace) {
  'use strict';

  var ROW_SELECTOR = '.invoice-line-row';
  var FORMSET_PREFIX = 'lines';

  function create(config) {
    var cfg = config || {};
    var wrapper = cfg.wrapper;
    var totalForms = cfg.totalForms;
    var articleUnitPriceMap = cfg.articleUnitPriceMap || {};
    var articleVatRateMap = cfg.articleVatRateMap || {};
    var articleMetaMap = cfg.articleMetaMap || {};
    var onTotalsChanged = cfg.onTotalsChanged || null;

    const { escapeHtml, formatAmount, parseAmountValue } = window.SaldoVibe.formUtils;

    function activeRows() {
      return Array.from(wrapper.querySelectorAll(ROW_SELECTOR));
    }

    function syncDeleteButtons() {
      const canDelete = activeRows().length > 1;
      wrapper.querySelectorAll('.delete-row-btn').forEach(function (btn) {
        btn.style.pointerEvents = canDelete ? '' : 'none';
        btn.style.opacity = canDelete ? '' : '0.35';
        btn.setAttribute('aria-disabled', canDelete ? 'false' : 'true');
        btn.title = canDelete ? 'Ta bort rad' : 'Minst en rad krävs';
      });
    }

    function updateRowOrder(row, index) {
      row.querySelectorAll('input, select, textarea, label').forEach(function (field) {
        if (field.name) {
          field.name = field.name.replace(/lines-(\d+|__prefix__)-/g, FORMSET_PREFIX + '-' + index + '-');
        }
        if (field.id) {
          field.id = field.id.replace(/id_lines-(\d+|__prefix__)-/g, 'id_' + FORMSET_PREFIX + '-' + index + '-');
        }
        if (field.htmlFor) {
          field.htmlFor = field.htmlFor.replace(
            /id_lines-(\d+|__prefix__)-/g,
            'id_' + FORMSET_PREFIX + '-' + index + '-'
          );
        }
      });

      const sortOrderInput = row.querySelector('input[name$="-sort_order"]');
      if (sortOrderInput) {
        sortOrderInput.value = String(index);
      }
    }

    function renumberRows() {
      activeRows().forEach(function (row, index) {
        updateRowOrder(row, index);
      });
      totalForms.value = String(activeRows().length);
    }

    function recalcRowTotals(row) {
      if (!row) {
        return;
      }
      const quantity = parseAmountValue(row.querySelector('input[name$="-quantity"]')?.value);
      const unitPrice = parseAmountValue(row.querySelector('input[name$="-unit_price"]')?.value);
      const vatRate = parseAmountValue(row.querySelector('input[name$="-vat_rate"]')?.value);
      const exVat = quantity * unitPrice;
      const vatAmount = exVat * (vatRate / 100);
      const total = exVat + vatAmount;

      const vatAmountEl = row.querySelector('.row-vat-amount');
      const totalEl = row.querySelector('.row-total-amount');
      if (vatAmountEl) {
        vatAmountEl.textContent = formatAmount(vatAmount);
      }
      if (totalEl) {
        totalEl.textContent = formatAmount(total);
      }

      if (onTotalsChanged) {
        onTotalsChanged();
      }
    }

    function recalcAllRows() {
      wrapper.querySelectorAll(ROW_SELECTOR).forEach(function (row) {
        recalcRowTotals(row);
      });
    }

    function applyDefaultUnitPriceForRow(row) {
      if (!row) {
        return;
      }
      const articleSelect = row.querySelector('select[name$="-article"]');
      const unitPriceInput = row.querySelector('input[name$="-unit_price"]');
      const vatRateInput = row.querySelector('input[name$="-vat_rate"]');
      const descriptionInput = row.querySelector('input[name$="-description"]');
      const unitInput = row.querySelector('input[name$="-unit"]');
      if (!articleSelect || !unitPriceInput) {
        return;
      }
      const selectedArticle = String(articleSelect.value || '');
      const unitPrice = articleUnitPriceMap[selectedArticle];
      const vatRate = articleVatRateMap[selectedArticle];
      const articleMeta = articleMetaMap[selectedArticle] || {};
      if (descriptionInput && articleMeta.name) {
        descriptionInput.value = articleMeta.name;
      }
      if (unitInput && articleMeta.unit) {
        unitInput.value = articleMeta.unit;
      }
      if (vatRateInput && typeof vatRate === 'string' && vatRate !== '') {
        vatRateInput.value = vatRate;
      }
      if (typeof unitPrice === 'string' && unitPrice !== '') {
        unitPriceInput.value = unitPrice;
      }
      recalcRowTotals(row);
    }

    function initSelect2(scope) {
      if (!window.jQuery || !jQuery.fn.select2) {
        return;
      }

      jQuery(scope).find('select#id_customer, select.invoice-article-select').each(function () {
        const $select = jQuery(this);
        if ($select.hasClass('select2-hidden-accessible')) {
          $select.select2('destroy');
        }
        const selectConfig = {
          width: '100%',
          dropdownAutoWidth: true,
        };
        if ($select.hasClass('invoice-article-select')) {
          selectConfig.escapeMarkup = function (markup) {
            return markup;
          };
          selectConfig.templateResult = function (state) {
            if (!state.id) {
              return state.text;
            }
            const meta = articleMetaMap[String(state.id)] || {};
            const articleNumber = escapeHtml(meta.number || '');
            const articleName = escapeHtml(meta.name || state.text || '');
            const description = escapeHtml(meta.description || '');
            const heading = articleNumber ? (articleNumber + ' ' + articleName) : articleName;
            if (!description) {
              return '<div>' + heading + '</div>';
            }
            return '<div><div>' + heading + '</div><div class="small text-muted">' + description + '</div></div>';
          };
          selectConfig.templateSelection = function (state) {
            if (!state.id) {
              return state.text;
            }
            const meta = articleMetaMap[String(state.id)] || {};
            const articleNumber = (meta.number || '').trim();
            const articleName = (meta.name || state.text || '').trim();
            return articleNumber ? (articleNumber + ' ' + articleName) : articleName;
          };
        }
        $select.select2(selectConfig);
        // Native required-validation anchors an English browser tooltip to the
        // select2-hidden <select> — or silently blocks the submit with no
        // feedback at all. Serverns svenska validering tar över istället.
        $select.removeAttr('required');
      });
    }

    function appendClonedRow(template) {
      const index = Number(totalForms.value);
      const newRow = template.content.firstElementChild.cloneNode(true);
      updateRowOrder(newRow, index);
      wrapper.appendChild(newRow);
      totalForms.value = String(index + 1);
      initSelect2(newRow);
      recalcRowTotals(newRow);
      syncDeleteButtons();
      return newRow;
    }

    function moveRow(row, direction) {
      if (!row) {
        return;
      }
      const sibling = direction === 'up' ? row.previousElementSibling : row.nextElementSibling;
      if (!sibling || !sibling.classList.contains('invoice-line-row')) {
        return;
      }
      if (direction === 'up') {
        wrapper.insertBefore(row, sibling);
      } else {
        wrapper.insertBefore(sibling, row);
      }
      renumberRows();
    }

    return {
      activeRows: activeRows,
      appendClonedRow: appendClonedRow,
      applyDefaultUnitPriceForRow: applyDefaultUnitPriceForRow,
      initSelect2: initSelect2,
      moveRow: moveRow,
      recalcAllRows: recalcAllRows,
      recalcRowTotals: recalcRowTotals,
      renumberRows: renumberRows,
      syncDeleteButtons: syncDeleteButtons,
      updateRowOrder: updateRowOrder,
    };
  }

  namespace.invoiceLines = { create: create };
})(window.SaldoVibe);
