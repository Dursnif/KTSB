import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import nb from './locales/nb.json';
import en from './locales/en.json';
import de from './locales/de.json';

i18n.use(initReactI18next).init({
  lng: localStorage.getItem('kaare_lang') || 'nb',
  fallbackLng: 'nb',
  resources: {
    nb: { translation: nb },
    en: { translation: en },
    de: { translation: de },
  },
  interpolation: { escapeValue: false },
});

export default i18n;
