"""TXT file translation"""
import os
import argparse

from yimt.api.utils import detect_lang


def translate_txt_auto(txt_fn, source_lang="auto", target_lang="zh", translation_file=None):
    if translation_file is None:
        paths = os.path.splitext(txt_fn)
        translated_txt_fn = paths[0] + "-translated" + paths[1]
    else:
        translated_txt_fn = translation_file

    txt = open(txt_fn, encoding="utf-8").read()

    if source_lang == "auto":
        source_lang = detect_lang(txt)

    from yimt.api.translators import Translators
    translator = Translators().get_translator(source_lang, target_lang)

    translation = translator.translate_paragraph(txt)

    out_f = open(translated_txt_fn, "w", encoding="utf-8")
    out_f.write(translation)
    out_f.close()

    return translated_txt_fn


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser("TXT File Translator")
    arg_parser.add_argument("--to_lang", type=str, default="zh", help="target language")
    arg_parser.add_argument("--input_file", type=str, required=True, help="file to be translated")
    arg_parser.add_argument("--output_file", type=str, default=None, help="translation file")
    args = arg_parser.parse_args()

    in_file = args.input_file
    out_file = args.output_file
    to_lang = args.to_lang

    translated_txt_fn = translate_txt_auto(in_file, target_lang=to_lang, translation_file=out_file)

    import webbrowser
    webbrowser.open(in_file)
    webbrowser.open(translated_txt_fn)