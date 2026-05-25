// KTSBLogo.jsx — Kåre The Smart Butler
//
// Single-file React component. Pair with ktsb-logo.css (import that into
// your app's global styles or root component once).
//
// Quick start:
//   import KTSBLogo from './KTSBLogo';
//   import './ktsb-logo.css';
//   <KTSBLogo size={280} />
//
// AI / runtime control — set from anywhere:
//   document.documentElement.dataset.mood = 'listening';
//   document.documentElement.style.setProperty('--ktsb-aperture', '0.4');
//   document.documentElement.style.setProperty('--ktsb-look-x', '12');
//
// Moods (also exported as KTSB_MOODS):
//   idle | watching | listening | thinking | happy | alert | dreaming | sleeping

import React, { useId } from 'react';

export const KTSB_MOODS = [
  { id: 'idle',      label: 'idle',      aperture: 1.0  },
  { id: 'watching',  label: 'watching',  aperture: 1.0  },
  { id: 'listening', label: 'listening', aperture: 0.95 },
  { id: 'thinking',  label: 'thinking',  aperture: 0.5  },
  { id: 'happy',     label: 'happy',     aperture: 0.85 },
  { id: 'alert',     label: 'alert',     aperture: 1.0  },
  { id: 'dreaming',  label: 'dreaming',  aperture: 0.35 },
  { id: 'sleeping',  label: 'sleeping',  aperture: 0.0  },
];

// ── Eye assembly (used by both standalone Eye and full Seal) ────────────
function EyeAssembly({ cx, cy, halfW, ctrlOffset, uid, showSpokes = true }) {
  const xL = cx - halfW, xR = cx + halfW;
  const yT = cy - ctrlOffset, yB = cy + ctrlOffset;
  const clipId = `ktsb-clip-${uid.replace(/[^a-z0-9-]/gi, '')}`;
  const eyeAxisVars = {
    '--k-eye-cx': `${cx}px`,
    '--k-eye-cy': `${cy}px`,
  };
  return (
    <g style={eyeAxisVars}>
      <defs>
        <clipPath id={clipId}>
          <path d={`M ${xL},${cy} Q ${cx},${yT} ${xR},${cy} Z`} className="k-upper-half" />
          <path d={`M ${xL},${cy} Q ${cx},${yB} ${xR},${cy} Z`} />
        </clipPath>
      </defs>
      <g className="k-eye-breathe">
        <path d={`M ${xL},${cy} Q ${cx},${yB} ${xR},${cy}`} className="k-fg k-med" fill="none" />
        <path d={`M ${xL},${cy} Q ${cx},${yT} ${xR},${cy}`} className="k-fg k-med k-upper-half" fill="none" />
        <g style={{ clipPath: `url(#${clipId})` }}>
          <circle cx={cx} cy={cy} r="22" className="k-fg k-hair k-ripple" />
          <circle cx={cx} cy={cy} r="22" className="k-fg k-hair k-ripple k-ripple-2" />
          <circle cx={cx} cy={cy} r="22" className="k-fg k-hair k-ripple k-ripple-3" />
          <g className="k-scan">
            <line x1={xL + 2} y1={cy} x2={xR - 2} y2={cy}
                  className="k-accent" strokeWidth="0.6" strokeDasharray="2 3" />
          </g>
          <g className="k-gaze">
            <circle cx={cx} cy={cy} r="var(--ktsb-iris-r)" className="k-iris k-thin" />
            {showSpokes && Array.from({ length: 8 }).map((_, i) => {
              const a = (i / 8) * Math.PI * 2;
              const r1 = 11, r2 = 19;
              const x1 = cx + Math.cos(a) * r1, y1 = cy + Math.sin(a) * r1;
              const x2 = cx + Math.cos(a) * r2, y2 = cy + Math.sin(a) * r2;
              return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} className="k-fg k-hair" />;
            })}
            <circle cx={cx} cy={cy} r="var(--ktsb-pupil-r)" className="k-pupil" />
          </g>
          <path
            d={`M ${cx - 24},${cy + ctrlOffset * 0.6} Q ${cx},${cy + ctrlOffset * 0.85} ${cx + 24},${cy + ctrlOffset * 0.6}`}
            className="k-smile k-thin" />
        </g>
      </g>
      <g className="k-orbit" style={{ transformOrigin: `${cx}px ${cy}px`, transformBox: 'view-box' }}>
        <circle cx={cx}      cy={cy - ctrlOffset * 0.95} r="1.6" className="k-accent" />
        <circle cx={cx + 28} cy={cy + ctrlOffset * 0.55} r="1.6" className="k-accent" />
        <circle cx={cx - 28} cy={cy + ctrlOffset * 0.55} r="1.6" className="k-accent" />
      </g>
    </g>
  );
}

// ── Icon-only (no wordmark) ─────────────────────────────────────────────
export function KTSBEye({ size = 200, variant = 'full', mood, style, className = '' }) {
  const uid = useId();
  const showOuter = variant === 'full';
  const showSpokes = variant !== 'glyph';
  const props = mood ? { 'data-mood': mood } : {};
  return (
    <span className={`ktsb ${className}`} style={style} {...props}>
      <svg viewBox="0 0 200 200" width={size} height={size} aria-label="KTSB" role="img">
        <circle cx="100" cy="100" r="96" className="k-fg k-hair k-aura" />
        {showOuter && Array.from({ length: 12 }).map((_, i) => {
          const a = (i / 12) * Math.PI * 2 - Math.PI / 2;
          const r1 = 92, r2 = 86;
          const x1 = 100 + Math.cos(a) * r1, y1 = 100 + Math.sin(a) * r1;
          const x2 = 100 + Math.cos(a) * r2, y2 = 100 + Math.sin(a) * r2;
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2}
                       className={`k-fg ${i % 3 === 0 ? 'k-thin' : 'k-hair'}`} />;
        })}
        {showOuter && <circle cx="100" cy="100" r="82" className="k-fg k-hair" />}
        <EyeAssembly cx={100} cy={100} halfW={62} ctrlOffset={38} uid={uid} showSpokes={showSpokes} />
        <text x="148" y="58" className="k-zzz">Z Z Z</text>
      </svg>
    </span>
  );
}

// ── Full lockup (KTSB + tagline inside the seal) ────────────────────────
export function KTSBLogo({ size = 280, variant = 'full', mood, style, className = '' }) {
  const uid = useId();
  const showOuter = variant === 'full';
  const props = mood ? { 'data-mood': mood } : {};
  return (
    <span className={`ktsb ${className}`} style={style} {...props}>
      <svg viewBox="0 0 280 280" width={size} height={size} aria-label="KTSB · Kåre The Smart Butler" role="img">
        <circle cx="140" cy="140" r="135" className="k-fg k-hair k-aura" />
        {showOuter && Array.from({ length: 12 }).map((_, i) => {
          const a = (i / 12) * Math.PI * 2 - Math.PI / 2;
          const r1 = 128, r2 = 122;
          const x1 = 140 + Math.cos(a) * r1, y1 = 140 + Math.sin(a) * r1;
          const x2 = 140 + Math.cos(a) * r2, y2 = 140 + Math.sin(a) * r2;
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2}
                       className={`k-fg ${i % 3 === 0 ? 'k-thin' : 'k-hair'}`} />;
        })}
        <circle cx="140" cy="140" r="118" className="k-fg k-thin" />
        <line x1="60" y1="182" x2="220" y2="182" className="k-fg k-hair" />
        <circle cx="60"  cy="182" r="1.4" className="k-accent" />
        <circle cx="220" cy="182" r="1.4" className="k-accent" />
        <EyeAssembly cx={140} cy={108} halfW={86} ctrlOffset={53} uid={uid} showSpokes={variant !== 'glyph'} />
        <text x="206" y="62" className="k-zzz">Z Z Z</text>
        <text x="140" y="218" textAnchor="middle"
          style={{ fontFamily: "'Cinzel', 'Trajan Pro', serif", fontWeight: 600,
                   fontSize: 36, letterSpacing: '0.22em', fill: 'var(--ktsb-fg)' }}>
          KTSB
        </text>
        <text x="140.5" y="229" textAnchor="middle"
          style={{ fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                   fontSize: 5.6, letterSpacing: '0.28em', fill: 'var(--ktsb-fg)', opacity: 0.6 }}>
          KÅRE · THE · SMART · BUTLER
        </text>
      </svg>
    </span>
  );
}

export default KTSBLogo;
