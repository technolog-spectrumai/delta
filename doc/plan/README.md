# Delta - dokumentacja LaTeX

## Zawartość

- `delta_wymagania_funkcjonalne.tex` - główny dokument LaTeX
- `elearning_i18n.sty` - styl dokumentacji e-learningowej
- `assets/` - 19 obrazów wydobytych z pliku DOCX
- `comments_extracted.json` - komentarze wydobyte z dokumentu źródłowego
- `delta_wymagania_funkcjonalne.pdf` - skompilowany dokument

## Kompilacja

```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error delta_wymagania_funkcjonalne.tex
```

Do kompilacji potrzebna jest dystrybucja TeX Live zawierająca m.in. `tcolorbox`, `graphicx`, `babel-polish`, `listings`, `caption` i `hyperref`.
