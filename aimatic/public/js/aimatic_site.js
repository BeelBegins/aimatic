(() => {
  "use strict";

  const site = document.querySelector(".aimatic-site");
  if (!site) return;
  site.classList.add("js-enabled");

  const header = site.querySelector("[data-header]");
  const menuToggle = site.querySelector("[data-menu-toggle]");
  const mobileNav = site.querySelector("[data-mobile-nav]");

  const updateHeader = () => {
    header?.classList.toggle("is-scrolled", window.scrollY > 12);
  };
  updateHeader();
  window.addEventListener("scroll", updateHeader, { passive: true });

  menuToggle?.addEventListener("click", () => {
    const open = mobileNav.classList.toggle("is-open");
    mobileNav.setAttribute("aria-hidden", String(!open));
    menuToggle.classList.toggle("is-open", open);
    menuToggle.setAttribute("aria-expanded", String(open));
    menuToggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
  });

  mobileNav?.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => {
      mobileNav.setAttribute("aria-hidden", "true");
      mobileNav.classList.remove("is-open");
      menuToggle?.classList.remove("is-open");
      menuToggle?.setAttribute("aria-expanded", "false");
      menuToggle?.setAttribute("aria-label", "Open navigation");
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !mobileNav?.classList.contains("is-open")) return;
    mobileNav.classList.remove("is-open");
    mobileNav.setAttribute("aria-hidden", "true");
    menuToggle?.classList.remove("is-open");
    menuToggle?.setAttribute("aria-expanded", "false");
    menuToggle?.setAttribute("aria-label", "Open navigation");
    menuToggle?.focus();
  });

  const revealItems = site.querySelectorAll("[data-reveal]");
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries, currentObserver) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const delay = entry.target.dataset.revealDelay;
        if (delay) entry.target.style.transitionDelay = `${delay}ms`;
        entry.target.classList.add("is-visible");
        currentObserver.unobserve(entry.target);
      });
    }, { threshold: 0.12 });
    revealItems.forEach((item) => observer.observe(item));
  } else {
    revealItems.forEach((item) => item.classList.add("is-visible"));
  }

  const form = site.querySelector("#aimatic-lead-form");
  const status = site.querySelector("#lead-form-status");
  const submitButton = form?.querySelector(".form-submit");
  const csrfToken = form?.dataset.csrf || window.frappe?.csrf_token || "Guest";

  const setStatus = (message, error = false) => {
    status?.classList.toggle("error", error);
    if (status) status.textContent = message;
  };

  const errorMessage = (payload) => {
    if (payload?._server_messages) {
      try {
        const messages = JSON.parse(payload._server_messages)
          .map((item) => typeof item === "string" ? JSON.parse(item) : item)
          .map((item) => item.message)
          .filter(Boolean);
        if (messages.length) return messages.join(" ");
      } catch {
        // Fall through to the generic response when server messages are malformed.
      }
    }
    return payload?.message || "We couldn't send your enquiry. Please try again.";
  };

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    if (data.get("website")) return;

    const name = String(data.get("name") || "").trim();
    const email = String(data.get("email") || "").trim();
    const company = String(data.get("company") || "").trim();
    const need = String(data.get("need") || "").trim();
    const message = String(data.get("message") || "").trim();
    if (!name || !email || !company || !need) {
      setStatus("Please complete the required fields before continuing.", true);
      return;
    }

    const payload = {
      name,
      email,
      company,
      need,
      message,
      website: String(data.get("website") || ""),
    };
    if (submitButton) submitButton.disabled = true;
    form.setAttribute("aria-busy", "true");
    setStatus("Sending your introduction…");

    try {
      const response = await fetch(form.dataset.endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-Frappe-CSRF-Token": csrfToken,
        },
        body: JSON.stringify(payload),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || result.exc) throw new Error(errorMessage(result));
      form.reset();
      setStatus(result.message?.message || "Thanks — we will be in touch shortly.");
    } catch (error) {
      const errorText = error instanceof Error ? error.message : "We couldn't send your enquiry.";
      setStatus(`${errorText} You can also email hello@aimatic.tech directly.`, true);
    } finally {
      if (submitButton) submitButton.disabled = false;
      form.removeAttribute("aria-busy");
    }
  });
})();
