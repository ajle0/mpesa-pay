// Keep the "Pay with M-Pesa · Ksh X" button in sync with the selected package.
document.addEventListener("change", (event) => {
  const radio = event.target.closest("[data-pay-form] input[name=package]");
  if (!radio) return;
  const label = radio.form.querySelector("[data-pay-amount]");
  if (label) label.textContent = "Ksh " + Number(radio.dataset.price).toLocaleString("en-KE");
});
