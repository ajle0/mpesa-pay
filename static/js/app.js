// Keep the pay button in sync with the amount typed: "Pay with M-Pesa · Ksh 1,500".
// The server re-validates the amount; this is only a convenience for the customer.
document.addEventListener("input", (event) => {
  const input = event.target.closest("[data-pay-amount-input]");
  if (!input) return;
  const label = input.form.querySelector("[data-pay-label]");
  if (!label) return;
  const digits = input.value.replace(/[\s,]/g, "").replace(/^(kes|ksh)/i, "");
  const amount = /^\d+$/.test(digits) ? Number(digits) : 0;
  label.textContent = amount > 0 ? `Pay with M-Pesa · Ksh ${amount.toLocaleString("en-KE")}` : "Pay with M-Pesa";
});

// Restore the label if the page is re-rendered with an amount already filled in.
document.addEventListener("DOMContentLoaded", () => {
  const input = document.querySelector("[data-pay-amount-input]");
  if (input && input.value) input.dispatchEvent(new Event("input", { bubbles: true }));
});

// HTMX ignores error responses by default, which made the Pay button look dead.
// Show the customer what happened instead.
function showPayError(event, message) {
  const form = event.detail.elt && event.detail.elt.closest && event.detail.elt.closest("[data-pay-form]");
  const slot = form && form.querySelector("#form-error");
  if (!slot) return;
  const p = document.createElement("p");
  p.className = "rounded-xl bg-red-50 px-3 py-2.5 text-sm text-red-700";
  p.setAttribute("role", "alert");
  p.textContent = message;
  slot.replaceChildren(p);
}

document.addEventListener("htmx:responseError", (event) => {
  const status = event.detail.xhr ? event.detail.xhr.status : 0;
  showPayError(
    event,
    status === 403
      ? "This page has expired. Refresh the page and try again."
      : "Something went wrong on our side. Please try again in a moment.",
  );
});

document.addEventListener("htmx:sendError", (event) => {
  showPayError(event, "Can't reach the server. Check your connection and try again.");
});
