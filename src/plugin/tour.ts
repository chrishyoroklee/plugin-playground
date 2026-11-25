// The quick tour: four cards that open on a first visit, while Python is still starting,
// and say what the page is for. Skip, Escape or the last card's button closes it, and
// this browser remembers that it was seen; Help, Quick tour, opens it again.
//
// The cards and their little animations are in index.html (#tour) and plugin.css
// (.tour). The dialog is a native <dialog> opened modally, so the browser keeps focus
// inside it and Escape closes it.

const KEY = 'plugin-playground-tour';

function seen(): boolean {
  try {
    return localStorage.getItem(KEY) !== null;
  } catch {
    // Storage blocked: better never to show it than to show it on every visit.
    return true;
  }
}

function remember(): void {
  try {
    localStorage.setItem(KEY, 'seen');
  } catch {
    // Storage blocked; it closes all the same.
  }
}

export interface Tour {
  open(): void;
  isOpen(): boolean;
}

export function mountTour(): Tour {
  const dialog = document.getElementById('tour') as HTMLDialogElement;
  const steps = [...dialog.querySelectorAll<HTMLElement>('.tour-step')];
  const dots = dialog.querySelector<HTMLElement>('.tour-dots')!;
  const back = document.getElementById('tour-back') as HTMLButtonElement;
  const next = document.getElementById('tour-next') as HTMLButtonElement;
  let at = 0;

  const dotButtons = steps.map((step, i) => {
    const dot = document.createElement('button');
    dot.type = 'button';
    dot.setAttribute('aria-label', `Step ${i + 1}: ${step.querySelector('h2')?.textContent ?? ''}`);
    dot.addEventListener('click', () => show(i));
    dots.append(dot);
    return dot;
  });

  function show(i: number): void {
    at = Math.max(0, Math.min(steps.length - 1, i));
    steps.forEach((step, j) => { step.hidden = j !== at; });
    dotButtons.forEach((dot, j) => dot.setAttribute('aria-current', String(j === at)));
    back.hidden = at === 0;
    next.textContent = at === steps.length - 1 ? 'Start making' : 'Next';
  }

  function close(): void {
    remember();
    if (dialog.open) dialog.close();
  }

  function open(): void {
    show(0);
    if (dialog.open) return;
    dialog.showModal();
    next.focus();
  }

  back.addEventListener('click', () => show(at - 1));
  next.addEventListener('click', () => (at === steps.length - 1 ? close() : show(at + 1)));
  document.getElementById('tour-skip')!.addEventListener('click', close);
  // Escape: the browser closes the dialog; it counts as seen.
  dialog.addEventListener('close', remember);
  // A click on the dimmed page around the card closes it too.
  dialog.addEventListener('click', (e) => { if (e.target === dialog) close(); });
  dialog.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight') show(at + 1);
    else if (e.key === 'ArrowLeft') show(at - 1);
    else return;
    e.preventDefault();
  });

  show(0);
  if (!seen()) open();
  return { open, isOpen: () => dialog.open };
}
