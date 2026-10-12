export type TargetLanguage = "zh-Hant" | "zh-Hans";
export type StyleVariants = Record<TargetLanguage, string>;
export type TranslationPreferences = {
    target_language: TargetLanguage;
    translation_style: string;
    translation_styles?: StyleVariants;
};
export function switchTarget(value: TranslationPreferences, target: TargetLanguage, defaults: StyleVariants): TranslationPreferences {
    const styles = {...defaults, ...value.translation_styles, [value.target_language]: value.translation_style};
    return {...value, target_language: target, translation_style: styles[target], translation_styles: styles};
}
