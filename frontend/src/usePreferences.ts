import {useEffect,useState} from "react";
import {locale,setLocale,type Locale} from "./i18n";
export function usePreferences() {
  const [language,updateLanguage]=useState<Locale>(locale),[theme,updateTheme]=useState(()=>localStorage.getItem("ownsei:theme")==="mocha"?"mocha":"classic");
  useEffect(()=>{
    document.documentElement.lang=language==="zh-Hans"?"zh-CN":"zh-TW";
    document.documentElement.dataset.theme=theme;
  },[language,theme]);
  useEffect(()=>{const update=()=>updateLanguage(locale());window.addEventListener("ownsei:preferences",update);return()=>window.removeEventListener("ownsei:preferences",update);},[]);
  return {language,theme,setLanguage:(value:Locale)=>{setLocale(value);updateLanguage(value);},setTheme:(value:string)=>{localStorage.setItem("ownsei:theme",value);updateTheme(value);}};
}
