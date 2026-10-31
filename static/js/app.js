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
