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

const FLIGHT_TARGET_SELECTOR = [
  ".why-trust-heading",
  ".promise-card-grid",
  ".trust-boundary-grid",
  ".trust-verification-band",
  ".learn-intro",
  ".learn-scroll-card",
  ".quiz-panel",
  ".faq-heading",
  ".faq-shell",
  ".landing-footer-main",
  ".landing-footer-bottom",
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
    const activeFlights = new WeakMap<HTMLElement, Animation>();
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
    let lastScrollUiAt = 0;
    let lastWheelAt = 0;
    let wheelLockUntil = 0;
    let lastScrollY = window.scrollY;
    let scrollDirection: 1 | -1 = 1;
    let latestPointerEvent: PointerEvent | null = null;
    let disposed = false;

    function animateFlight(element: HTMLElement, orderOverride?: number) {
      if (activeFlights.has(element)) return;
      const reduced = reducedMotionQuery.matches;
      const order =
        orderOverride ?? (Number.parseInt(element.style.getPropertyValue("--tl-reveal-order"), 10) || 0);
      const isCard = element.matches(
        ".promise-card-grid, .trust-boundary-grid, .trust-verification-band, .faq-shell, .landing-footer-main, .landing-footer-bottom",
      );
      const isCompact = element.matches(
        ".agent-segmented, .evidence-points > div, .faq-group, .landing-footer-column",
      );
      const isStatement = element.matches(
        ".trust-heading, .why-trust-heading, .faq-heading, .report-showcase-heading, .one-agent-heading, .architecture-heading, .evidence-heading",
      );
      const side = order % 2 === 0 ? -1 : 1;
      const cardSlots = [-96, -48, 48, 96];
      const distanceScale = reduced ? 0.58 : 1;
      const x = (isCard
        ? cardSlots[Math.min(order, cardSlots.length - 1)]
        : isCompact
          ? side * 42
          : isStatement
            ? 0
            : side * 66) * distanceScale;
      const y =
        (isCard ? 96 : isCompact ? 54 : isStatement ? 82 : 68) *
        distanceScale *
        scrollDirection;
      const scale = isCard ? 0.9 : isCompact ? 0.96 : isStatement ? 0.92 : 0.94;
      const animation = element.animate(
        [
          {
            opacity: 0.04,
            translate: `${x}px ${y}px`,
            scale: String(scale),
          },
          {
            opacity: 1,
            translate: "0px 0px",
            scale: "1",
          },
        ],
        {
          delay: Math.min(order * 60, 240),
          duration: reduced ? 480 : 720,
          easing: "cubic-bezier(0.16, 1, 0.3, 1)",
          fill: "both",
        },
      );
      animation.id = "tl-flight-in";
      activeFlights.set(element, animation);
      animation.onfinish = () => {
        if (activeFlights.get(element) === animation) {
          activeFlights.delete(element);
        }
      };
    }

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

    const buildLandingStops = () => {
      const viewportHeight = Math.max(1, window.innerHeight);
      const maxScroll = Math.max(0, document.documentElement.scrollHeight - viewportHeight);
      const stops = new Set<number>([0]);
      const how = document.querySelector<HTMLElement>(".home > .how-narrative");
      if (how) {
        const howTop = how.offsetTop;
        const howTravel = Math.max(0, how.offsetHeight - viewportHeight);
        [0, 0.25, 0.5, 0.75, 1].forEach((progress) => {
          stops.add(howTop + howTravel * progress);
        });
      }

      document
        .querySelectorAll<HTMLElement>(
          ".home > .why-trust-section, .home > .who-uses-section, .home > .learn-section, .home > .faq-section, .home > .landing-footer",
        )
        .forEach((section) => {
          stops.add(section.offsetTop);
          const overflow = Math.max(0, section.offsetHeight - viewportHeight);
          if (overflow > 360) {
            stops.add(section.offsetTop + overflow);
          }
        });

      return [...stops]
        .map((stop) => clamp(Math.round(stop), 0, maxScroll))
        .sort((a, b) => a - b)
        .filter((stop, index, values) => index === 0 || Math.abs(stop - values[index - 1]) > 220);
    };

    const animateWindowTo = (target: number) => {
      window.clearTimeout(pageScrollEndTimer);
      pageMoving = true;
      root.style.scrollBehavior = "auto";
      window.scrollTo({ top: target, behavior: "auto" });
      root.style.removeProperty("scroll-behavior");
      pageScrollEndTimer = window.setTimeout(finishPageMove, 60);
    };

    const finishPageMove = () => {
      if (!pageMoving) return;
      pageMoving = false;
      window.clearTimeout(pageScrollEndTimer);
      queueSyncSegments();
      replayVisibleFlights();
    };

    const replayVisibleFlights = () => {
      const viewportHeight = Math.max(1, window.innerHeight);
      const targets = Array.from(document.querySelectorAll<HTMLElement>(`.home ${FLIGHT_TARGET_SELECTOR}`));
      let animated = 0;
      targets.forEach((element) => {
        if (animated >= 6) return;
        if (element.closest(".how-scene[aria-hidden='true']")) return;
        const rect = element.getBoundingClientRect();
        if (rect.bottom <= 24 || rect.top >= viewportHeight * 0.92) return;
        const style = window.getComputedStyle(element);
        if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return;
        activeFlights.get(element)?.cancel();
        activeFlights.delete(element);
        animateFlight(element, animated);
        animated += 1;
      });
    };

    const onLandingWheel = (event: WheelEvent) => {
      if (!document.querySelector(".home") || !finePointerQuery.matches || window.innerWidth < 900) return;
      if (event.ctrlKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
      if (
        closestElement(
          event.target,
          ".merged-card-scroll, .report-evidence-modal, .account-dialog, .global-search-results",
        )
      ) {
        return;
      }

      const now = performance.now();
      const delta = event.deltaY;
      if (Math.abs(delta) < 6) return;
      if (now < wheelLockUntil || pageMoving || now - lastWheelAt < 150) {
        lastWheelAt = now;
        if (event.cancelable) event.preventDefault();
        return;
      }

      const stops = buildLandingStops();
      const current = window.scrollY;
      const direction = delta > 0 ? 1 : -1;
      const nextStop =
        direction > 0
          ? stops.find((stop) => stop > current + 18)
          : [...stops].reverse().find((stop) => stop < current - 18);
      if (nextStop == null) return;

      if (event.cancelable) event.preventDefault();
      lastWheelAt = now;
      wheelLockUntil = now + 650;
      animateWindowTo(nextStop);
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
        scrollDirection = nextScrollY > lastScrollY ? 1 : -1;
        lastScrollY = nextScrollY;
      }
      if (scrollFrame) return;
      scrollFrame = window.requestAnimationFrame(() => {
        scrollFrame = 0;
        updateScrollProgress();
        const now = performance.now();
        if (now - lastScrollUiAt > 80) {
          lastScrollUiAt = now;
          queueSyncSegments();
        }
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
    window.addEventListener("wheel", onLandingWheel, { passive: false });
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
      window.removeEventListener("wheel", onLandingWheel);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("scrollend", onScrollEnd);
      window.removeEventListener("resize", onResize);
      finePointerQuery.removeEventListener("change", syncEnvironment);
      reducedMotionQuery.removeEventListener("change", syncEnvironment);
      if (pointerFrame) window.cancelAnimationFrame(pointerFrame);
      if (segmentFrame) window.cancelAnimationFrame(segmentFrame);
      if (scrollFrame) window.cancelAnimationFrame(scrollFrame);
      window.clearTimeout(pageScrollEndTimer);
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
