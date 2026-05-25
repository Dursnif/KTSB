import type React from "react";
import { useAuth } from "../auth/AuthContext";
import { readUserPrefs } from "../hooks/useUserPrefs";

export type ThemeRole = "child" | "teen" | "young_adult" | "adult" | "admin";

export interface Theme {
  primary: string;
  bubbleUserBg: string;
  bubbleKareShadow: string;
  bubbleKareBorder: string;
  inputFocusShadow: string;
  inputFocusBorder: string;
  titleCss: React.CSSProperties;
  btnBg: string;
  typingColor: string;
  keyframes: string;
  msgAnimation: string;
  chatFontSize: string;
}

const themes: Record<ThemeRole, Theme> = {
  // 4–10 år: fargerik, spretten, stor og rund
  child: {
    primary: "#ff6b9d",
    bubbleUserBg: "linear-gradient(135deg, #ff6b9d, #ff9a3c)",
    bubbleKareShadow: "0 0 14px #ff6b9d33",
    bubbleKareBorder: "1px solid #ff6b9d44",
    inputFocusShadow: "0 0 0 2px #ff6b9d55, 0 0 18px #ff6b9d1a",
    inputFocusBorder: "#ff6b9d88",
    titleCss: {
      background: "linear-gradient(90deg, #ff6b9d, #ffd93d)",
      WebkitBackgroundClip: "text",
      WebkitTextFillColor: "transparent",
      backgroundClip: "text",
    } as React.CSSProperties,
    btnBg: "linear-gradient(135deg, #ff6b9d, #ff9a3c)",
    typingColor: "#ff6b9d",
    chatFontSize: "15px",
    keyframes: `
      @keyframes msgIn {
        from { opacity: 0; transform: scale(0.85) translateY(8px); }
        to   { opacity: 1; transform: scale(1)    translateY(0);  }
      }
      @keyframes bounceDot {
        0%, 80%, 100% { transform: scale(0.8);  opacity: 0.5; }
        40%           { transform: scale(1.3);  opacity: 1;   }
      }
    `,
    msgAnimation: "msgIn 0.35s cubic-bezier(0.34,1.56,0.64,1)",
  },

  // 11–17 år: neon, mørk, glow, gradient
  teen: {
    primary: "#646cff",
    bubbleUserBg: "linear-gradient(135deg, #646cff, #8b5cf6)",
    bubbleKareShadow: "0 0 18px #646cff28, 0 0 0 1px #646cff1a",
    bubbleKareBorder: "1px solid #646cff44",
    inputFocusShadow: "0 0 0 2px #646cff55, 0 0 20px #646cff18",
    inputFocusBorder: "#646cff99",
    titleCss: {
      background: "linear-gradient(90deg, #646cff, #c084fc)",
      WebkitBackgroundClip: "text",
      WebkitTextFillColor: "transparent",
      backgroundClip: "text",
    } as React.CSSProperties,
    btnBg: "linear-gradient(135deg, #646cff, #8b5cf6)",
    typingColor: "#646cff",
    chatFontSize: "15px",
    keyframes: `
      @keyframes msgIn {
        from { opacity: 0; transform: translateY(10px); }
        to   { opacity: 1; transform: translateY(0);   }
      }
      @keyframes bounceDot {
        0%, 80%, 100% { transform: translateY(0);   }
        40%           { transform: translateY(-7px); }
      }
    `,
    msgAnimation: "msgIn 0.22s ease",
  },

  // 18–25 år: stilren blå gradient, subtil
  young_adult: {
    primary: "#4f9cf9",
    bubbleUserBg: "linear-gradient(135deg, #4f9cf9, #818cf8)",
    bubbleKareShadow: "0 0 10px #4f9cf91a",
    bubbleKareBorder: "1px solid #4f9cf933",
    inputFocusShadow: "0 0 0 2px #4f9cf944",
    inputFocusBorder: "#4f9cf977",
    titleCss: {
      background: "linear-gradient(90deg, #4f9cf9, #818cf8)",
      WebkitBackgroundClip: "text",
      WebkitTextFillColor: "transparent",
      backgroundClip: "text",
    } as React.CSSProperties,
    btnBg: "linear-gradient(135deg, #4f9cf9, #818cf8)",
    typingColor: "#4f9cf9",
    chatFontSize: "15px",
    keyframes: `
      @keyframes msgIn {
        from { opacity: 0; transform: translateY(6px); }
        to   { opacity: 1; transform: translateY(0);  }
      }
      @keyframes bounceDot {
        0%, 80%, 100% { transform: translateY(0);   }
        40%           { transform: translateY(-5px); }
      }
    `,
    msgAnimation: "msgIn 0.2s ease",
  },

  // Voksen: rent, enkelt, ingen ekstra effekter
  adult: {
    primary: "#646cff",
    bubbleUserBg: "#646cff",
    bubbleKareShadow: "none",
    bubbleKareBorder: "none",
    inputFocusShadow: "none",
    inputFocusBorder: "#444",
    titleCss: { color: "#fff" },
    btnBg: "#646cff",
    typingColor: "#555",
    chatFontSize: "15px",
    keyframes: `
      @keyframes msgIn {
        from { opacity: 0; }
        to   { opacity: 1; }
      }
      @keyframes bounceDot {
        0%, 60%, 100% { opacity: 0.35; }
        30%           { opacity: 1;    }
      }
    `,
    msgAnimation: "msgIn 0.15s ease",
  },

  // Admin: identisk med adult
  admin: {
    primary: "#646cff",
    bubbleUserBg: "#646cff",
    bubbleKareShadow: "none",
    bubbleKareBorder: "none",
    inputFocusShadow: "none",
    inputFocusBorder: "#444",
    titleCss: { color: "#fff" },
    btnBg: "#646cff",
    typingColor: "#555",
    chatFontSize: "15px",
    keyframes: `
      @keyframes msgIn {
        from { opacity: 0; }
        to   { opacity: 1; }
      }
      @keyframes bounceDot {
        0%, 60%, 100% { opacity: 0.35; }
        30%           { opacity: 1;    }
      }
    `,
    msgAnimation: "msgIn 0.15s ease",
  },
};

// Used on the login page before the user is known
export const loginTheme: Theme = themes.teen;

const FONT_SIZES: Record<string, string> = { small: "13px", normal: "15px", large: "17px" };

const MINIMAL_KEYFRAMES = `
  @keyframes msgIn { from { opacity: 0; } to { opacity: 1; } }
  @keyframes bounceDot { 0%, 60%, 100% { opacity: 0.35; } 30% { opacity: 1; } }
`;

export function useTheme(): Theme {
  const { user } = useAuth();
  const role = (user?.role ?? "adult") as ThemeRole;
  const base = themes[role] ?? themes.adult;

  const prefs = readUserPrefs();
  const accent = prefs.accentColor;
  const fontSize = FONT_SIZES[prefs.fontSize] ?? "15px";
  const minimal = prefs.animations === "minimal";

  if (!accent && fontSize === "15px" && !minimal) return base;

  return {
    ...base,
    ...(accent ? {
      primary: accent,
      bubbleUserBg: accent,
      btnBg: accent,
      typingColor: accent,
      titleCss: { color: accent },
    } : {}),
    chatFontSize: fontSize,
    ...(minimal ? {
      bubbleKareShadow: "none",
      bubbleKareBorder: "none",
      inputFocusShadow: "none",
      keyframes: MINIMAL_KEYFRAMES,
      msgAnimation: "msgIn 0.12s ease",
    } : {}),
  };
}
