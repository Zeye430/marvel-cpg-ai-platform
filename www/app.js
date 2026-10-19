// Apple Pro Number Shuffling & Deceleration Animation
function initNumberShuffle() {
  const elements = document.querySelectorAll('.shuffle-number');
  if (!elements.length) return;

  elements.forEach((el, index) => {
    const rawTarget = el.getAttribute('data-target');
    if (!rawTarget || isNaN(parseFloat(rawTarget))) return;

    const targetVal = parseFloat(rawTarget);
    const prefix = el.getAttribute('data-prefix') || '';
    const suffix = el.getAttribute('data-suffix') || '';
    const decimals = parseInt(el.getAttribute('data-decimals') || '1', 10);

    // Staggered settle timing: each card locks in 180ms after the previous one
    const baseDuration = 1000;
    const duration = baseDuration + (index * 180);
    const startTime = performance.now();

    // Cache corresponding delta pill to reveal after settling
    const parentTile = el.closest('.kpi-tile');
    const deltaPill = parentTile ? parentTile.querySelector('.kpi-delta-pill') : null;
    if (deltaPill) {
      deltaPill.style.opacity = '0.2';
      deltaPill.style.transition = 'opacity 0.4s ease';
    }

    function step(now) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1.0);

      if (progress < 1.0) {
        // Shuffling phase
        if (progress > 0.65) {
          // Decelerating phase: approaching target with decaying jitter
          const jitterAmp = (1.0 - progress) * (Math.abs(targetVal) * 0.4 + 2);
          const jitter = (Math.random() - 0.5) * jitterAmp;
          const current = (targetVal * progress) + jitter;
          const sign = (current < 0 && !prefix.includes('-')) ? '-' : (prefix === '+' && current > 0 ? '+' : '');
          el.textContent = `${sign}${Math.abs(current).toFixed(decimals)}${suffix}`;
        } else {
          // Fast random slot-machine shuffle phase
          const minVal = targetVal >= 0 ? 5 : -25;
          const maxVal = targetVal >= 0 ? 98 : 15;
          const randVal = (Math.random() * (maxVal - minVal) + minVal);
          const sign = (randVal < 0) ? '-' : (prefix === '+' ? '+' : '');
          el.textContent = `${sign}${Math.abs(randVal).toFixed(decimals)}${suffix}`;
        }
        requestAnimationFrame(step);
      } else {
        // Locked settled phase
        const sign = (targetVal < 0) ? '-' : (prefix === '+' && targetVal > 0 ? '+' : '');
        el.textContent = `${sign}${Math.abs(targetVal).toFixed(decimals)}${suffix}`;
        el.classList.add('shuffle-settled');

        // Reveal delta pill with crisp pop
        if (deltaPill) {
          deltaPill.style.opacity = '1';
        }
      }
    }
    requestAnimationFrame(step);
  });

  // Secondary unit number shuffler (e.g. -42,046 units)
  const unitElements = document.querySelectorAll('.shuffle-units');
  unitElements.forEach((el, index) => {
    const rawVal = el.getAttribute('data-target');
    if (!rawVal || isNaN(parseFloat(rawVal))) return;
    const targetInt = parseInt(rawVal, 10);
    const suffix = el.getAttribute('data-suffix') || '';
    const duration = 1400;
    const startTime = performance.now();

    function stepUnits(now) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1.0);
      if (progress < 1.0) {
        const randOffset = Math.floor((Math.random() - 0.5) * 50000 * (1 - progress));
        const currentInt = Math.round(targetInt * progress) + randOffset;
        const sign = currentInt >= 0 ? '+' : '';
        el.textContent = `${sign}${currentInt.toLocaleString()}${suffix}`;
        requestAnimationFrame(stepUnits);
      } else {
        const sign = targetInt >= 0 ? '+' : '';
        el.textContent = `${sign}${targetInt.toLocaleString()}${suffix}`;
      }
    }
    requestAnimationFrame(stepUnits);
  });
}

// Hook into Shiny life-cycle events
$(document).on('shiny:value', function(event) {
  if (event.name === 'kpi_row') {
    setTimeout(initNumberShuffle, 60);
  }
});

$(document).on('shiny:inputchanged', function(event) {
  if (event.name === 'auth_state' && event.value === 'logged_in') {
    setTimeout(function() {
      initNumberShuffle();
      window.dispatchEvent(new Event('resize'));
    }, 180);
  }
});

// Window load fallback
window.addEventListener('load', function() {
  setTimeout(initNumberShuffle, 200);
});