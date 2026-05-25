import { useEffect, useRef } from 'react';

type BehaviorDef = {
  baseX: number;
  baseY: number;
  driftX: number;
  driftY: number;
  ms: number;
};

const BEHAVIORS: Record<string, BehaviorDef> = {
  idle:      { baseX: -12, baseY: -8,  driftX: 8,  driftY: 5,  ms: 3200 },
  watching:  { baseX:   0, baseY:  0,  driftX: 4,  driftY: 4,  ms: 4500 },
  listening: { baseX:   0, baseY: -7,  driftX: 4,  driftY: 3,  ms: 1800 },
  thinking:  { baseX:   0, baseY:  0,  driftX: 22, driftY: 8,  ms: 650  },
  happy:     { baseX:   4, baseY: -7,  driftX: 6,  driftY: 4,  ms: 2400 },
  alert:     { baseX:   0, baseY:  0,  driftX: 14, driftY: 6,  ms: 380  },
  dreaming:  { baseX: -16, baseY: -5,  driftX: 12, driftY: 7,  ms: 5000 },
  sleeping:  { baseX:   0, baseY: 10,  driftX: 2,  driftY: 2,  ms: 9000 },
};

const setLook = (x: number, y: number) => {
  document.documentElement.style.setProperty('--ktsb-look-x', String(Math.round(x)));
  document.documentElement.style.setProperty('--ktsb-look-y', String(Math.round(y)));
};

const jitter = (base: number, drift: number) =>
  base + (Math.random() * 2 - 1) * drift;

export function usePupilAnimation(options?: { loginFocus?: boolean }): void {
  const timerRef     = useRef<ReturnType<typeof setTimeout> | null>(null);
  const focusRef     = useRef<ReturnType<typeof setTimeout> | null>(null);
  const moodRef      = useRef<string>('idle');
  const readyRef     = useRef(!options?.loginFocus); // false until focus phase ends

  const schedule = (mood: string) => {
    if (!readyRef.current) return;
    if (timerRef.current) clearTimeout(timerRef.current);

    const def = BEHAVIORS[mood] ?? BEHAVIORS['idle'];

    const tick = () => {
      setLook(jitter(def.baseX, def.driftX), jitter(def.baseY, def.driftY));
      const next = def.ms * (0.7 + Math.random() * 0.6);
      timerRef.current = setTimeout(tick, next);
    };

    setLook(jitter(def.baseX, def.driftX), jitter(def.baseY, def.driftY));
    const next = def.ms * (0.7 + Math.random() * 0.6);
    timerRef.current = setTimeout(tick, next);
  };

  useEffect(() => {
    const observer = new MutationObserver(mutations => {
      for (const m of mutations) {
        if (m.type === 'attributes' && m.attributeName === 'data-mood') {
          const next = (m.target as HTMLElement).dataset.mood ?? 'idle';
          if (next !== moodRef.current) {
            moodRef.current = next;
            schedule(next);
          }
        }
      }
    });

    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-mood'],
    });

    if (options?.loginFocus) {
      // Center the pupil and hold for 2.5s — "Kåre looks at you"
      setLook(0, 0);
      focusRef.current = setTimeout(() => {
        readyRef.current = true;
        const mood = document.documentElement.dataset.mood ?? 'idle';
        moodRef.current = mood;
        schedule(mood);
      }, 2500);
    } else {
      const initial = document.documentElement.dataset.mood ?? 'idle';
      moodRef.current = initial;
      schedule(initial);
    }

    return () => {
      observer.disconnect();
      if (timerRef.current) clearTimeout(timerRef.current);
      if (focusRef.current) clearTimeout(focusRef.current);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
