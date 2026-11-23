import { readPreference, writePreference } from "./interfacePreferences";
import {useEffect,useState} from "react";
import {locale,localePreference,setLocale,type LocalePreference} from "./i18n";
export function usePreferences() {
  const [language,updateLanguage]=useState(locale), [languagePreference,updatePreference]=useState(localePreference);
  const [theme,updateTheme]=useState(()=>readPreference("ownsei:theme")==="mocha"?"mocha":"classic");
  useEffect(()=>{
    document.documentElement.lang=language==="zh-Hans"?"zh-CN":"zh-TW";
    document.documentElement.dataset.theme=theme;
    const favicon=document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if(favicon)favicon.href=theme==="mocha"?"/ownsei-logo-dark.png":"/ownsei-logo.png";
  },[language,theme]);
  useEffect(()=>{
    const update=()=>{updateLanguage(locale());updatePreference(localePreference());};
    window.addEventListener("ownsei:preferences",update);
    window.addEventListener("languagechange",update);
    return()=>{
      window.removeEventListener("ownsei:preferences",update);
      window.removeEventListener("languagechange",update);
    };
  },[]);
  return {language,languagePreference,theme,setLanguage:(value:LocalePreference)=>{
    setLocale(value);updateLanguage(locale());updatePreference(localePreference());
  },setTheme:(value:string)=>{writePreference("ownsei:theme",value);updateTheme(value);}};
}
