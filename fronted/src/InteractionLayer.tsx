import { useEffect } from "react";

const TILT_SELECTOR = [
  ".trust-card",
  ".cover-card",
  ".promise-card:not(.who-use-card)",
  ".team-related-card",
  ".decision-card",
].join(",");

const MAGNETIC_SELECTOR = [
  ".landing-nav-cta",
  ".landing-nav-mobile-cta",
  ".landing-team-entry",
  ".home-fixed-cta",
  ".hero-cta",
  ".hero-send-button",
  ".hero-add-button",
  ".evidence-cta",
  ".agent-send-button",
  ".report-final-action > button",
  ".new-analysis-submit",
  ".creator-submit-button",
  ".who-use-action:not(:disabled)",
  ".quiz-result-next",
  ".quiz-result-actions > button:first-child",
  ".team-related-card > footer button",
  ".team-batch-bar button",
  ".team-drawer > footer button",
].join(",");

const RIPPLE_SELECTOR = [
  "button:not(:disabled):not(.logo):not(.landing-brand):not(.landing-menu-toggle)",
  "[role='button']:not([aria-disabled='true'])",
].join(",");

const REVEAL_SELECTORS = [
  ".agent-intro-heading",
  ".agent-prompt-card",
  ".agent-intro-reply-live",
  ".agent-example-row",
  ".trust-heading",
  ".trust-card",
  ".evidence-heading",
  ".evidence-points > div",
  ".evidence-card",
  ".one-agent-heading",
  ".agent-segmented",
  ".agent-scene-content",
  ".architecture-heading",
  ".aperture-column",
  ".merge-output",
  ".report-showcase-heading",
  ".why-trust-heading",
  ".promise-card",
  ".trust-boundary-grid > section",
  ".trust-verification-band",
  ".who-uses-section .why-trust-heading",
  ".learn-intro",
  ".learn-scroll-card",
  ".quiz-panel",
  ".faq-heading",
  ".faq-group",
  ".landing-footer-brand-area",
  ".landing-footer-column",
  ".workspace-header",
  ".agent-symbol",
  ".hello",
  ".workspace-center h2",
  ".workspace-intro",
  ".new-analysis-composer",
  ".creator-self-check > header",
  ".creator-form",
  ".chat-header",
  ".analysis-process-progress",
  ".trus-council",
  ".message-row",
  ".result-header",
  ".unified-report-page > header",
  ".live-report-summary",
  ".report-section-block",
  ".decision-card",
  ".team-page-heading",
  ".team-summary",
  ".team-view-switch",
  ".team-filters",
  ".team-queue",
  ".team-related-groups",
  ".creator-report-sheet",
].join(",");

const SEGMENT_SELECTORS = [
  ".landing-nav-links",
  ".analysis-mode-switch",
  ".workspace-mode-switch",
  ".agent-segmented",
  ".report-anchor-nav",
  ".team-role-switch",
  ".team-view-switch",
].join(",");

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

function closestElement(target: EventTarget | null, selector: string): HTMLElement | null {
  if (!(target instanceof Element)) return null;
  return target.closest(selector) as HTMLElement | null;
}

function resetTilt(element: HTMLElement | null) {
  if (!element) return;
  element.classList.remove("tl-tilting");
  element.style.removeProperty("--tl-tilt-x");
  element.style.removeProperty("--tl-tilt-y");
  element.style.removeProperty("--tl-glow-x");
  element.style.removeProperty("--tl-glow-y");
}

function resetMagnetic(element: HTMLElement | null) {
  if (!element) return;
  element.classList.remove("tl-magnetic-active");
  element.style.removeProperty("--tl-mag-x");
  element.style.removeProperty("--tl-mag-y");
}

export default function InteractionLayer() {
  useEffect(() => {
    const root = document.documentElement;
    const finePointerQuery = window.matchMedia("(pointer: fine) and (hover: hover)");
    const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const revealObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("tl-in-view");
          revealObserver.unobserve(entry.target);
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.08 },
    );

    let activeTilt: HTMLElement | null = null;
    let activeMagnetic: HTMLElement | null = null;
    let landingRevealElements: HTMLElement[] = [];
    let pointerFrame = 0;
    let segmentFrame = 0;
    let scrollFrame = 0;
    let pageScrollEndTimer = 0;
    let pageMoving = false;
    let lastScrollY = window.scrollY;
    let latestPointerEvent: PointerEvent | null = null;
    let scrollingTimer = 0;
    let disposed = false;

    const syncEnvironment = () => {
      const fine = finePointerQuery.matches;
      const reduced = reducedMotionQuery.matches;
      const landing = Boolean(document.querySelector(".home"));
      root.classList.toggle("tl-fine-pointer", fine);
      root.classList.toggle("tl-reduced-motion", reduced);
      root.classList.toggle("tl-page-landing", landing);
      document.body.classList.toggle("tl-touch-input", !fine);
      if (!fine || reduced) {
        resetTilt(activeTilt);
        resetMagnetic(activeMagnetic);
        activeTilt = null;
        activeMagnetic = null;
      }
    };

    const scanReveals = () => {
      document.querySelectorAll<HTMLElement>(REVEAL_SELECTORS).forEach((element) => {
        if (element.dataset.tlRevealReady === "true") {
          if (!element.classList.contains("tl-in-view")) revealObserver.observe(element);
          return;
        }
        const siblings = element.parentElement
          ? Array.from(element.parentElement.children).filter((child) => child.matches(REVEAL_SELECTORS))
          : [];
        const siblingIndex = Math.max(0, siblings.indexOf(element));
        element.dataset.tlRevealReady = "true";
        element.classList.add("tl-reveal");
        element.style.setProperty("--tl-reveal-order", String(Math.min(siblingIndex, 7)));
        revealObserver.observe(element);
      });
      landingRevealElements = Array.from(document.querySelectorAll<HTMLElement>(".home .tl-reveal"));
    };

    const scanInteractionTargets = () => {
      document.querySelectorAll<HTMLElement>(TILT_SELECTOR).forEach((element) => {
        element.classList.add("tl-tilt-capable");
      });
      document.querySelectorAll<HTMLElement>(MAGNETIC_SELECTOR).forEach((element) => {
        element.classList.add("tl-magnetic");
      });
    };

    const syncSegments = () => {
      segmentFrame = 0;
      document.querySelectorAll<HTMLElement>(SEGMENT_SELECTORS).forEach((container) => {
        const active = container.querySelector<HTMLElement>("button.active, button[aria-selected='true'], [aria-current='page']");
        if (!active) {
          container.classList.remove("tl-segment-ready");
          return;
        }
        container.style.setProperty("--tl-segment-x", `${active.offsetLeft}px`);
        container.style.setProperty("--tl-segment-y", `${active.offsetTop}px`);
        container.style.setProperty("--tl-segment-width", `${active.offsetWidth}px`);
        container.style.setProperty("--tl-segment-height", `${active.offsetHeight}px`);
        container.classList.add("tl-segment", "tl-segment-ready");
      });
    };

    const queueSyncSegments = () => {
      if (segmentFrame) return;
      segmentFrame = window.requestAnimationFrame(syncSegments);
    };

    const updateScrollProgress = () => {
      const maxScroll = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
      const progress = clamp(window.scrollY / maxScroll, 0, 1);
      root.style.setProperty("--tl-scroll-progress", String(progress));
    };

    /**
     * Flag the document as mid-scroll so CSS can drop the expensive
     * `backdrop-filter` panels (see interaction.css). Re-arms on every scroll
     * event and only clears once the user has been idle for a beat, so a
     * continuous drag keeps them off instead of flickering back on.
     */
    const markScrolling = () => {
      root.classList.add("tl-scrolling");
      window.clearTimeout(scrollingTimer);
      scrollingTimer = window.setTimeout(() => {
        scrollingTimer = 0;
        root.classList.remove("tl-scrolling");
        // One layout read once the page has settled, instead of every 80ms.
        queueSyncSegments();
      }, 160);
    };

    const animateWindowTo = (target: number) => {
      window.clearTimeout(pageScrollEndTimer);
      pageMoving = true;
      // Native smooth scroll: sections arrive by scrolling instead of a jump plus a fly-in.
      window.scrollTo({ top: target, behavior: reducedMotionQuery.matches ? "auto" : "smooth" });
      pageScrollEndTimer = window.setTimeout(finishPageMove, reducedMotionQuery.matches ? 60 : 1200);
    };

    const finishPageMove = () => {
      if (!pageMoving) return;
      pageMoving = false;
      window.clearTimeout(pageScrollEndTimer);
      queueSyncSegments();
    };

    const updatePointer = () => {
      pointerFrame = 0;
      const event = latestPointerEvent;
      if (!event || disposed) return;
      const fine = finePointerQuery.matches;
      const reduced = reducedMotionQuery.matches;
      root.style.setProperty("--tl-pointer-x", `${event.clientX}px`);
      root.style.setProperty("--tl-pointer-y", `${event.clientY}px`);

      const interactionTarget = closestElement(
        event.target,
        "a, button, input, textarea, select, [role='button'], .trust-card, .promise-card, .team-related-card",
      );
      root.classList.toggle("tl-pointer-hot", Boolean(interactionTarget));

      const hero = closestElement(event.target, ".cinematic-hero");
      if (hero && fine && !reduced) {
        root.style.setProperty("--tl-hero-pointer-x", String(event.clientX / window.innerWidth - 0.5));
        root.style.setProperty("--tl-hero-pointer-y", String(event.clientY / window.innerHeight - 0.5));
      }

      const nextTilt = fine && !reduced ? closestElement(event.target, TILT_SELECTOR) : null;
      if (activeTilt !== nextTilt) {
        resetTilt(activeTilt);
        activeTilt = nextTilt;
        if (activeTilt) activeTilt.classList.add("tl-tilting");
      }
      if (activeTilt) {
        const rect = activeTilt.getBoundingClientRect();
        const xRatio = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1);
        const yRatio = clamp((event.clientY - rect.top) / Math.max(1, rect.height), 0, 1);
        activeTilt.style.setProperty("--tl-tilt-x", `${(0.5 - yRatio) * 2.4}deg`);
        activeTilt.style.setProperty("--tl-tilt-y", `${(xRatio - 0.5) * 3.2}deg`);
        activeTilt.style.setProperty("--tl-glow-x", `${xRatio * 100}%`);
        activeTilt.style.setProperty("--tl-glow-y", `${yRatio * 100}%`);
      }

      const nextMagnetic = fine && !reduced ? closestElement(event.target, MAGNETIC_SELECTOR) : null;
      if (activeMagnetic !== nextMagnetic) {
        resetMagnetic(activeMagnetic);
        activeMagnetic = nextMagnetic;
        if (activeMagnetic) activeMagnetic.classList.add("tl-magnetic-active");
      }
      if (activeMagnetic) {
        const rect = activeMagnetic.getBoundingClientRect();
        const x = clamp((event.clientX - (rect.left + rect.width / 2)) * 0.12, -7, 7);
        const y = clamp((event.clientY - (rect.top + rect.height / 2)) * 0.14, -5, 5);
        activeMagnetic.style.setProperty("--tl-mag-x", `${x}px`);
        activeMagnetic.style.setProperty("--tl-mag-y", `${y}px`);
      }
    };

    const onPointerMove = (event: PointerEvent) => {
      if (event.pointerType !== "mouse") return;
      latestPointerEvent = event;
      if (!pointerFrame) pointerFrame = window.requestAnimationFrame(updatePointer);
    };

    const onPointerLeave = () => {
      root.classList.remove("tl-pointer-hot");
      resetTilt(activeTilt);
      resetMagnetic(activeMagnetic);
      activeTilt = null;
      activeMagnetic = null;
    };

    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      const button = closestElement(event.target, RIPPLE_SELECTOR);
      if (!button || button.matches(":disabled, [aria-disabled='true']")) return;
      const rect = button.getBoundingClientRect();
      const size = Math.min(180, Math.max(rect.width, rect.height) * 1.55);
      const ripple = document.createElement("span");
      ripple.className = "tl-ripple";
      ripple.style.width = `${size}px`;
      ripple.style.height = `${size}px`;
      ripple.style.setProperty("--tl-ripple-x", `${event.clientX - rect.left}px`);
      ripple.style.setProperty("--tl-ripple-y", `${event.clientY - rect.top}px`);
      button.classList.add("tl-ripple-host");
      button.appendChild(ripple);
      window.setTimeout(() => ripple.remove(), 680);
    };

    const onDocumentClick = (event: MouseEvent) => {
      queueSyncSegments();
      window.setTimeout(queueSyncSegments, 80);
      window.setTimeout(queueSyncSegments, 320);
      const navButton = closestElement(
        event.target,
        ".landing-nav-links button, .landing-mobile-menu > button, .landing-footer-column > button, .landing-footer-brand",
      );
      if (!navButton) return;
      const label = navButton.textContent?.replace(/[↗→]/g, "").trim();
      const sectionIds: Record<string, string> = {
        "产品介绍": "how-journey",
        "Why Trust Us": "why-trust",
        "信任依据": "why-trust",
        "谁在用": "who-uses",
        "美妆鉴别指南": "learn",
        "FAQ": "faq",
        "常见问题 FAQ": "faq",
      };
      if (navButton.classList.contains("landing-footer-brand")) {
        window.setTimeout(() => animateWindowTo(0), 0);
        return;
      }
      const sectionId = sectionIds[label ?? ""];
      if (!sectionId) return;
      const section = document.getElementById(sectionId);
      if (!section) return;
      window.setTimeout(() => {
        animateWindowTo(section.offsetTop);
      }, 0);
      window.setTimeout(() => {
        queueSyncSegments();
      }, 620);
    };

    const onScroll = () => {
      const nextScrollY = window.scrollY;
      if (Math.abs(nextScrollY - lastScrollY) > 1) {
        lastScrollY = nextScrollY;
        markScrolling();
      }
      if (scrollFrame) return;
      scrollFrame = window.requestAnimationFrame(() => {
        scrollFrame = 0;
        // Only the progress bar depends on scroll position. `syncSegments` reads
        // offsetLeft/offsetTop of every segment control, which forces a sync
        // layout — scrolling cannot move those, so it waits for the scroll to
        // settle (see markScrolling's trailing timer).
        updateScrollProgress();
      });
    };

    const onResize = () => {
      queueSyncSegments();
    };

    const onScrollEnd = () => {
      if (pageMoving) finishPageMove();
    };

    const mutationObserver = new MutationObserver(() => {
      syncEnvironment();
      scanReveals();
      scanInteractionTargets();
      queueSyncSegments();
      updateScrollProgress();
    });

    syncEnvironment();
    scanReveals();
    scanInteractionTargets();
    queueSyncSegments();
    updateScrollProgress();
    window.requestAnimationFrame(() => root.classList.add("tl-ui-ready"));

    document.addEventListener("pointermove", onPointerMove, { passive: true });
    document.addEventListener("pointerdown", onPointerDown, { passive: true });
    document.addEventListener("click", onDocumentClick, { passive: true });
    document.addEventListener("pointerleave", onPointerLeave);
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("scrollend", onScrollEnd);
    window.addEventListener("resize", onResize);
    finePointerQuery.addEventListener("change", syncEnvironment);
    reducedMotionQuery.addEventListener("change", syncEnvironment);
    mutationObserver.observe(document.body, { childList: true, subtree: true });

    return () => {
      disposed = true;
      revealObserver.disconnect();
      mutationObserver.disconnect();
      document.removeEventListener("pointermove", onPointerMove);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("click", onDocumentClick);
      document.removeEventListener("pointerleave", onPointerLeave);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("scrollend", onScrollEnd);
      window.removeEventListener("resize", onResize);
      finePointerQuery.removeEventListener("change", syncEnvironment);
      reducedMotionQuery.removeEventListener("change", syncEnvironment);
      if (pointerFrame) window.cancelAnimationFrame(pointerFrame);
      if (segmentFrame) window.cancelAnimationFrame(segmentFrame);
      if (scrollFrame) window.cancelAnimationFrame(scrollFrame);
      window.clearTimeout(pageScrollEndTimer);
      window.clearTimeout(scrollingTimer);
      root.classList.remove("tl-scrolling");
      resetTilt(activeTilt);
      resetMagnetic(activeMagnetic);
      root.classList.remove(
        "tl-ui-ready",
        "tl-fine-pointer",
        "tl-reduced-motion",
        "tl-page-landing",
        "tl-pointer-hot",
      );
      document.body.classList.remove("tl-touch-input");
    };
  }, []);

  return (
    <>
      <div aria-hidden="true" className="tl-pointer-layer">
        <span className="tl-pointer-spotlight" />
        <span className="tl-pointer-lens"><i /></span>
      </div>
      <span aria-hidden="true" className="tl-scroll-progress" />
    </>
  );
}
