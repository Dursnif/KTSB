import React from 'react';

export type KTSBMood = 'idle' | 'watching' | 'listening' | 'thinking' | 'happy' | 'alert' | 'dreaming' | 'sleeping';
export type KTSBVariant = 'full' | 'glyph';

export interface KTSBLogoProps {
  size?: number;
  variant?: KTSBVariant;
  mood?: KTSBMood;
  style?: React.CSSProperties;
  className?: string;
}

export interface KTSBEyeProps {
  size?: number;
  variant?: KTSBVariant;
  mood?: KTSBMood;
  style?: React.CSSProperties;
  className?: string;
}

export declare const KTSB_MOODS: Array<{ id: KTSBMood; label: string; aperture: number }>;
export declare function KTSBEye(props: KTSBEyeProps): React.ReactElement;
export declare function KTSBLogo(props: KTSBLogoProps): React.ReactElement;
export default KTSBLogo;
