/* Shared behaviour for the "välj bilagor" attachment picker.
 *
 * Five forms let the user attach receipts: verifikationer, bankbokföring,
 * leverantörsfakturor, kundfakturor and utlägg. Picking an attachment navigates away to
 * the picker page and back, so each form has to stash its unsaved input in sessionStorage
 * and restore it on the way back. The list widget, the picker link and the round-trip
 * plumbing are identical on all five; only the formset shape and the post-restore
 * recalculation differ, and those stay in the page as callbacks.
 */
window.SaldoVibe = window.SaldoVibe || {};

(function (namespace) {
  'use strict';

  // The picker page's URL, used to tell "came back from the picker" apart from a
  // plain page load — restoring stashed state on an unrelated visit would clobber
  // whatever the user is typing.
  var PICKER_PATH_MARKER = '/bilagor/valj/';
  var EMPTY_STATE_ID = 'no-selected-attachments';
  var SKIPPED_FIELD_NAMES = ['csrfmiddlewaretoken', 'selected_attachment_ids'];

  function resolveElement(value, fallbackId) {
    var target = value || fallbackId;
    if (!target) {
      return null;
    }
    return typeof target === 'string' ? document.getElementById(target) : target;
  }

  function formFields(form) {
    return form.querySelectorAll('input[name], select[name], textarea[name]');
  }

  /* The selected-attachment list: remove buttons, empty state and picker link.
   *
   * Returns the helpers so a page can re-sync after mutating the list itself.
   */
  function initSelectionList(options) {
    var opts = options || {};
    var idsInput = resolveElement(opts.idsInput, 'selected-attachment-ids');
    var list = resolveElement(opts.list, 'selected-attachments-list');
    var pickerLink = resolveElement(opts.pickerLink, 'open-attachment-picker');
    // The document-entry forms render an attachment viewer panel that listens
    // for these.
    var viewerEvents = Boolean(opts.viewerEvents);

    function currentSelectedIds() {
      var raw = (idsInput && idsInput.value) ? idsInput.value : '';
      return raw.split(',').map(function (value) { return value.trim(); }).filter(Boolean);
    }

    function syncPickerLink() {
      if (!pickerLink || !idsInput) {
        return;
      }
      var url = new URL(pickerLink.href, window.location.origin);
      url.searchParams.set('selected', idsInput.value || '');
      pickerLink.href = url.pathname + url.search;
    }

    function ensureEmptyState() {
      if (!list) {
        return;
      }
      var hasRows = list.querySelector('[data-attachment-id]');
      var emptyState = document.getElementById(EMPTY_STATE_ID);
      if (hasRows && emptyState) {
        emptyState.remove();
      }
      if (!hasRows && !emptyState) {
        var div = document.createElement('div');
        div.id = EMPTY_STATE_ID;
        div.className = 'list-group-item text-muted small';
        div.textContent = 'Inga bilagor valda.';
        list.appendChild(div);
      }
    }

    if (list && idsInput) {
      list.addEventListener('click', function (event) {
        var row = event.target.closest('[data-attachment-id]');
        if (!row) {
          return;
        }
        var rowId = row.getAttribute('data-attachment-id');

        if (event.target.closest('.remove-selected-attachment')) {
          idsInput.value = currentSelectedIds()
            .filter(function (id) { return id !== rowId; })
            .join(',');
          row.remove();
          ensureEmptyState();
          syncPickerLink();
          if (viewerEvents) {
            document.dispatchEvent(
              new CustomEvent('attachment-viewer:remove', { detail: { id: Number(rowId) } })
            );
          }
          return;
        }

        if (viewerEvents) {
          event.preventDefault();
          document.dispatchEvent(
            new CustomEvent('attachment-viewer:show', { detail: { id: Number(rowId) } })
          );
        }
      });
    }

    ensureEmptyState();
    syncPickerLink();

    return {
      currentSelectedIds: currentSelectedIds,
      syncPickerLink: syncPickerLink,
      ensureEmptyState: ensureEmptyState
    };
  }

  /* Snapshot every named field as {name: value}. File inputs are skipped — their
   * value cannot be assigned back — and multi-selects keep every chosen option.
   */
  function serializeFormState(form) {
    var state = {};
    formFields(form).forEach(function (field) {
      if (!field.name || SKIPPED_FIELD_NAMES.indexOf(field.name) !== -1) {
        return;
      }
      if (field.type === 'file') {
        return;
      }
      if (field.type === 'checkbox') {
        state[field.name] = field.checked;
        return;
      }
      if (field.tagName === 'SELECT' && field.multiple) {
        state[field.name] = Array.from(field.selectedOptions).map(function (option) {
          return option.value;
        });
        return;
      }
      state[field.name] = field.value;
    });
    return state;
  }

  function applyFormState(state, form) {
    formFields(form).forEach(function (field) {
      if (!field.name || !(field.name in state)) {
        return;
      }
      var value = state[field.name];
      if (field.type === 'checkbox') {
        field.checked = Boolean(value);
        return;
      }
      if (field.tagName === 'SELECT' && field.multiple && Array.isArray(value)) {
        Array.from(field.options).forEach(function (option) {
          option.selected = value.indexOf(option.value) !== -1;
        });
        return;
      }
      field.value = value;
    });
  }

  function cameBackFromPicker() {
    var url = new URL(window.location.href);
    return url.searchParams.has('selected_attachments')
      || document.referrer.indexOf(PICKER_PATH_MARKER) !== -1;
  }

  /* Stash the form on the way to the picker, restore it on the way back.
   *
   * Options: form, storageKey, and optionally serialize/apply (banking's form has
   * repeated field names and needs its own shape), growRows (add formset rows before
   * values land) and afterRestore (recalculate totals, re-init Select2, …).
   */
  function initFormStateRoundtrip(options) {
    var opts = options || {};
    var form = resolveElement(opts.form);
    var storageKey = opts.storageKey;
    var pickerLink = resolveElement(opts.pickerLink, 'open-attachment-picker');
    var serialize = opts.serialize || serializeFormState;
    var apply = opts.apply || applyFormState;

    if (!form || !storageKey) {
      return;
    }

    if (pickerLink) {
      pickerLink.addEventListener('click', function () {
        sessionStorage.setItem(storageKey, JSON.stringify(serialize(form)));
      });
    }

    var raw = sessionStorage.getItem(storageKey);
    if (!raw || !cameBackFromPicker()) {
      return;
    }

    var state;
    try {
      state = JSON.parse(raw);
    } catch (error) {
      sessionStorage.removeItem(storageKey);
      return;
    }

    if (typeof opts.growRows === 'function') {
      opts.growRows(state);
    }
    apply(state, form);
    if (typeof opts.afterRestore === 'function') {
      opts.afterRestore(state);
    }

    sessionStorage.removeItem(storageKey);
  }

  /* Disables the upload submit button and swaps its label for a spinner while the form
   * POSTs - same loading pattern as templates/bookkeeping/sie_import.html. The upload can
   * take several seconds: ReInvGrabber's OCR extraction runs synchronously as part of the
   * request (see attachments/services.py::save_attachment_with_thumbnail), so without this
   * a double-click re-submits the file and creates a duplicate attachment.
   */
  function initUploadLoadingState(options) {
    var opts = options || {};
    var form = resolveElement(opts.form);
    var submitBtn = resolveElement(opts.submitBtn);
    var label = resolveElement(opts.label);
    var loading = resolveElement(opts.loading);
    if (!form || !submitBtn || !label || !loading) {
      return;
    }

    form.addEventListener('submit', function () {
      label.classList.add('d-none');
      loading.classList.remove('d-none');
      // Disabling the submit button synchronously in the submit handler cancels the
      // submission in Safari, so defer it until after the browser has started
      // processing the form post (see templates/bookkeeping/sie_import.html).
      window.setTimeout(function () { submitBtn.disabled = true; }, 0);
    });
  }

  namespace.attachments = {
    initSelectionList: initSelectionList,
    initFormStateRoundtrip: initFormStateRoundtrip,
    initUploadLoadingState: initUploadLoadingState,
    serializeFormState: serializeFormState,
    applyFormState: applyFormState
  };
})(window.SaldoVibe);
