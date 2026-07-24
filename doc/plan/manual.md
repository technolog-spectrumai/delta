Instalacja środowiska do kompilacji dokumentacji Delta

Instrukcja opisuje przygotowanie środowiska LaTeX do kompilacji dokumentówkorzystających z pakietu:

elearning_documentation_i18n.sty

Przykładowy główny plik dokumentu:

delta_documentation_example.tex

Instrukcja obejmuje:

Linux: Debian/Ubuntu, Fedora oraz Arch Linux;

Windows: MiKTeX albo TeX Live;

kompilację z użyciem latexmk i pdflatex;

konfigurację logo;

rozwiązywanie najczęstszych problemów.

1. Wymagane elementy

Do kompilacji potrzebne są:

dystrybucja TeX/LaTeX:

TeX Live na Linuxie;

MiKTeX albo TeX Live na Windowsie;

kompilator pdflatex;

narzędzie latexmk — zalecane, ponieważ automatycznie wykonuje odpowiedniąliczbę przebiegów kompilatora;

plik stylu elearning_documentation_i18n.sty;

główny plik .tex;

plik logo w formacie PNG, JPG albo PDF.

Styl korzysta między innymi z pakietów:

amsmath
babel
booktabs
caption
enumitem
eso-pic
etoolbox
fancyhdr
geometry
graphicx
hyperref
listings
longtable
multirow
subcaption
tabularx
tcolorbox
titlesec
xfp
xparse
xurl

Pełna instalacja TeX Live zawiera wszystkie wymagane komponenty.

2. Zalecana struktura projektu

delta-documentation/
├── manual.md
├── delta_documentation.tex
├── elearning_documentation_i18n.sty
└── assets/
    └── delta-logo.png

Plik .sty powinien znajdować się:

w tym samym katalogu co główny plik .tex; albo

w lokalnym drzewie pakietów TeX.

Najprostszy wariant to trzymanie pliku .sty obok dokumentu .tex.

Konfiguracja logo w pliku .tex:

\ElearnLogoPath{assets/delta-logo.png}
\ElearnLogoWidthPercent{24}

Wartość 24 oznacza 24% szerokości obszaru tekstowego. Wysokość jest wyliczanaautomatycznie, a proporcje obrazu są zachowane.

Linux

3. Debian i Ubuntu

3.1. Instalacja zalecana

Pełna instalacja jest największa, ale eliminuje większość problemów z brakującymipakietami:

sudo apt update
sudo apt install texlive-full latexmk

Instalacja texlive-full może zajmować kilka gigabajtów.

3.2. Instalacja mniejszego zestawu

Gdy pełna dystrybucja jest zbyt duża:

sudo apt update
sudo apt install \
  texlive-latex-base \
  texlive-latex-recommended \
  texlive-latex-extra \
  texlive-fonts-recommended \
  texlive-lang-polish \
  latexmk

Ten zestaw powinien zawierać pakiety wykorzystywane przezelearning_documentation_i18n.sty.

3.3. Weryfikacja

pdflatex --version
latexmk --version
kpsewhich elearning_documentation_i18n.sty

Ostatnia komenda zwróci ścieżkę do pliku stylu tylko wtedy, gdy uruchomisz jąw katalogu projektu albo zainstalujesz styl w drzewie TeX.

4. Fedora

Najprostsza instalacja kompletnej dystrybucji:

sudo dnf install texlive-scheme-full latexmk

Po instalacji sprawdź:

pdflatex --version
latexmk --version

Jeżeli latexmk nie jest dostępny jako osobny pakiet w używanej wersji Fedory,sprawdź, czy został już dostarczony przez zainstalowany schemat TeX Live:

command -v latexmk

5. Arch Linux i dystrybucje pochodne

Zainstaluj pełny zestaw TeX Live oraz latexmk:

sudo pacman -Syu
sudo pacman -S texlive-meta latexmk

W zależności od aktualnego podziału pakietów w repozytorium może być równieżpotrzebny pakiet językowy dla języka polskiego:

sudo pacman -S texlive-langpolish

Sprawdzenie:

pdflatex --version
latexmk --version

6. Uniwersalna instalacja TeX Live na Linuxie

Dystrybucję TeX Live można również zainstalować bezpośrednio za pomocąoficjalnego instalatora, niezależnie od menedżera pakietów systemu.

Pobierz i rozpakuj instalator:

wget https://mirror.ctan.org/systems/texlive/tlnet/install-tl-unx.tar.gz
tar -xzf install-tl-unx.tar.gz
cd install-tl-*

Uruchom instalację:

sudo perl install-tl

Po instalacji dodaj katalog binarny TeX Live do zmiennej PATH. Dokładna ścieżkazależy od roku wydania i architektury, przykładowo:

export PATH=/usr/local/texlive/2026/bin/x86_64-linux:$PATH

Aby ustawienie było trwałe, dodaj je do jednego z plików:

~/.bashrc
~/.zshrc
~/.profile

Następnie:

source ~/.bashrc
pdflatex --version
latexmk --version

Nie należy mieszać bez potrzeby systemowej instalacji TeX Live z instalacjązarządzaną przez oficjalny tlmgr.

Windows

7. Wariant A — MiKTeX

MiKTeX jest zwykle najprostszym rozwiązaniem na Windowsie.

7.1. Instalacja

Pobierz Basic MiKTeX Installer z oficjalnej strony MiKTeX:https://miktex.org/download.

Uruchom instalator.

Wybierz instalację:

tylko dla bieżącego użytkownika, gdy nie masz uprawnień administratora;

dla wszystkich użytkowników, gdy komputer jest współdzielony.

Pozostaw domyślny format papieru A4.

Po instalacji uruchom MiKTeX Console.

W sekcji aktualizacji wykonaj pełną aktualizację pakietów.

W ustawieniach pakietów ustaw automatyczną instalację brakujących pakietów na:

Ask me first; albo

Always.

Styl wykorzystuje kilka pakietów dodatkowych, dlatego automatyczna instalacjabrakujących zależności jest wygodna.

7.2. Instalacja latexmk

W MiKTeX Console wyszukaj i zainstaluj pakiet:

latexmk

W aktualnych instalacjach MiKTeX latexmk może być instalowany jako pakietdystrybucji. Narzędzie wymaga również interpretera Perl.

Najprościej zainstalować Perl przez Strawberry Perl:

https://strawberryperl.com/

Po instalacji zamknij i ponownie otwórz PowerShell.

7.3. Weryfikacja w PowerShell

pdflatex --version
latexmk --version
perl --version

Jeżeli polecenia nie są rozpoznawane, uruchom ponownie terminal albo komputer.

8. Wariant B — TeX Live na Windowsie

TeX Live daje środowisko bardzo podobne do instalacji linuksowej.

Pobierz oficjalny instalator:https://mirror.ctan.org/systems/texlive/tlnet/install-tl-windows.exe.

Uruchom instalator.

Wybierz pełny schemat instalacji, jeżeli miejsce na dysku nie jest problemem.

Upewnij się, że instalator doda katalog TeX Live do zmiennej PATH.

Po instalacji otwórz nowy PowerShell.

Weryfikacja:

pdflatex --version
latexmk --version

Aktualizacja pakietów TeX Live:

tlmgr update --self
tlmgr update --all

PowerShell może wymagać uruchomienia z uprawnieniami administratora, jeżeliTeX Live został zainstalowany dla wszystkich użytkowników.

Kompilacja

9. Kompilacja zalecana przez latexmk

Przejdź do katalogu projektu.

Linux:

cd /ścieżka/do/delta-documentation

Windows PowerShell:

cd C:\ścieżka\do\delta-documentation

Uruchom:

latexmk -pdf -interaction=nonstopmode -file-line-error delta_documentation.tex

Dla dostarczonego pliku przykładowego:

latexmk -pdf -interaction=nonstopmode -file-line-error delta_documentation_example.tex

Wynikiem będzie:

delta_documentation.pdf

lub:

delta_documentation_example.pdf

Czyszczenie plików pomocniczych

Linux i Windows:

latexmk -c

Usunięcie również wygenerowanego PDF:

latexmk -C

10. Kompilacja bez latexmk

Można użyć bezpośrednio pdflatex:

pdflatex -interaction=nonstopmode -file-line-error delta_documentation.tex
pdflatex -interaction=nonstopmode -file-line-error delta_documentation.tex

Dwa przebiegi są potrzebne między innymi do poprawnego utworzenia spisu treści,numeracji oraz odnośników.

Gdy dokument zawiera bibliografię BibTeX:

pdflatex delta_documentation.tex
bibtex delta_documentation
pdflatex delta_documentation.tex
pdflatex delta_documentation.tex

Przy latexmk sekwencja ta jest zwykle wykonywana automatycznie.

11. Skrypt kompilacyjny dla Linuxa

Utwórz plik build.sh:

#!/usr/bin/env bash
set -euo pipefail

MAIN_FILE="${1:-delta_documentation.tex}"

if ! command -v latexmk >/dev/null 2>&1; then
    echo "Błąd: latexmk nie jest zainstalowany lub nie znajduje się w PATH."
    exit 1
fi

if [[ ! -f "$MAIN_FILE" ]]; then
    echo "Błąd: nie znaleziono pliku: $MAIN_FILE"
    exit 1
fi

latexmk \
    -pdf \
    -interaction=nonstopmode \
    -file-line-error \
    "$MAIN_FILE"

Nadaj uprawnienia:

chmod +x build.sh

Uruchom:

./build.sh

albo:

./build.sh delta_documentation_example.tex

12. Skrypt kompilacyjny dla Windows PowerShell

Utwórz plik build.ps1:

param(
    [string]$MainFile = "delta_documentation.tex"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command latexmk -ErrorAction SilentlyContinue)) {
    Write-Error "latexmk nie jest zainstalowany lub nie znajduje się w PATH."
}

if (-not (Test-Path $MainFile)) {
    Write-Error "Nie znaleziono pliku: $MainFile"
}

latexmk `
    -pdf `
    -interaction=nonstopmode `
    -file-line-error `
    $MainFile

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Uruchom:

powershell -ExecutionPolicy Bypass -File .\build.ps1

Dla pliku przykładowego:

powershell -ExecutionPolicy Bypass -File .\build.ps1 `
    -MainFile delta_documentation_example.tex

Konfiguracja dokumentu

13. Minimalny plik .tex

\documentclass[12pt,a4paper]{report}

\usepackage{elearning_documentation_i18n}

\ElearnPlatformName{Delta}
\ElearnOrganization{Delta Education}
\ElearnDocumentType{Dokumentacja platformy e-learningowej}
\ElearnDocumentTitle{Dokumentacja funkcjonalna i techniczna}
\ElearnDocumentSubtitle{
  Architektura, moduły, role użytkowników i procesy edukacyjne
}
\ElearnDocumentVersion{0.1}
\ElearnDocumentStatus{Wersja robocza}

\ElearnLogoPath{assets/delta-logo.png}
\ElearnLogoWidthPercent{24}

\begin{document}
\selectlanguage{polish}

\ElearnFrontMatter

\chapter{Wprowadzenie}

Treść dokumentacji.

\end{document}

14. Ścieżka do logo

Zalecana jest ścieżka względna:

\ElearnLogoPath{assets/delta-logo.png}

Ścieżka jest interpretowana względem katalogu roboczego kompilatora. Najlepiejuruchamiać kompilację z katalogu zawierającego główny plik .tex.

Poprawnie:

cd delta-documentation
latexmk -pdf delta_documentation.tex

Potencjalnie błędnie:

latexmk -pdf delta-documentation/delta_documentation.tex

W drugim przypadku ścieżka assets/delta-logo.png może zostać rozwiązanawzględem niewłaściwego katalogu.

Na Windowsie w poleceniach LaTeX zalecane są ukośniki /:

\ElearnLogoPath{C:/projekty/delta/assets/delta-logo.png}

Lepszym i przenośnym rozwiązaniem pozostaje ścieżka względna.

Obsługiwane formaty przy pdflatex:

.png
.jpg
.jpeg
.pdf

Format SVG wymaga konwersji albo dodatkowego pakietu i zewnętrznego narzędzia.Najprościej wyeksportować logo do PDF lub PNG.

Edytory

15. Visual Studio Code

Opcjonalnie można zainstalować:

Visual Studio Code;

rozszerzenie LaTeX Workshop.

Przykładowa konfiguracja .vscode/settings.json:

{
  "latex-workshop.latex.tools": [
    {
      "name": "latexmk",
      "command": "latexmk",
      "args": [
        "-pdf",
        "-interaction=nonstopmode",
        "-synctex=1",
        "-file-line-error",
        "%DOC%"
      ]
    }
  ],
  "latex-workshop.latex.recipes": [
    {
      "name": "latexmk PDF",
      "tools": ["latexmk"]
    }
  ],
  "latex-workshop.latex.recipe.default": "lastUsed"
}

Rozszerzenie używa lokalnie zainstalowanego TeX Live lub MiKTeX. Samorozszerzenie nie instaluje kompilatora LaTeX.

16. TeXstudio

TeXstudio jest opcjonalnym edytorem graficznym dostępnym na Linuxie i Windowsie.

W konfiguracji kompilacji ustaw:

latexmk -pdf -interaction=nonstopmode -file-line-error %.tex

TeXstudio również wymaga osobnej instalacji TeX Live lub MiKTeX.

Rozwiązywanie problemów

17. File elearning_documentation_i18n.sty not found

Przykładowy komunikat:

LaTeX Error: File `elearning_documentation_i18n.sty' not found.

Rozwiązanie:

skopiuj elearning_documentation_i18n.sty do katalogu głównego dokumentu;

uruchom kompilację z tego katalogu.

Sprawdzenie na Linuxie:

ls -l elearning_documentation_i18n.sty
kpsewhich elearning_documentation_i18n.sty

Sprawdzenie na Windowsie:

Get-Item .\elearning_documentation_i18n.sty
kpsewhich elearning_documentation_i18n.sty

18. Brak pakietu LaTeX

Przykładowy komunikat:

LaTeX Error: File `tcolorbox.sty' not found.

Debian/Ubuntu

Zainstaluj pakiety dodatkowe:

sudo apt install texlive-latex-extra

TeX Live instalowany oficjalnym instalatorem

tlmgr install tcolorbox

Dla innych brakujących pakietów:

tlmgr search --global --file '/nazwa-pakietu.sty'
tlmgr install nazwa-pakietu

MiKTeX

Otwórz MiKTeX Console.

Przejdź do listy pakietów.

Wyszukaj pakiet.

Zainstaluj go.

Odśwież bazę nazw plików, jeżeli MiKTeX tego wymaga.

Można również włączyć automatyczną instalację brakujących pakietów.

19. latexmk nie jest rozpoznawany

Linux:

command -v latexmk
echo "$PATH"

Windows:

Get-Command latexmk
$env:PATH

Po instalacji zamknij wszystkie terminale i otwórz nowy.

Przy MiKTeX upewnij się również, że zainstalowano Perl:

perl --version

20. Logo nie jest wyświetlane

Sprawdź:

czy plik istnieje;

czy nazwa pliku ma poprawną wielkość liter — Linux rozróżnia wielkie i małelitery;

czy kompilacja jest uruchamiana z katalogu projektu;

czy format to PNG, JPG albo PDF;

czy ścieżka nie zawiera literówki.

Linux:

test -f assets/delta-logo.png && echo "Logo istnieje"

Windows:

Test-Path .\assets\delta-logo.png

W logu kompilacji może pojawić się ostrzeżenie:

Logo file `...' not found; title-page logo omitted

Brak logo nie powinien zatrzymać kompilacji dokumentu.

21. Polskie znaki są niepoprawne

Plik .tex i .sty powinny być zapisane w kodowaniu UTF-8.

Sprawdź, czy dokument zawiera:

\usepackage{elearning_documentation_i18n}

Pakiet stylu ładuje:

\RequirePackage[T1]{fontenc}
\RequirePackage[utf8]{inputenc}
\RequirePackage[polish,english]{babel}

W treści dokumentu wybierz język:

\selectlanguage{polish}

22. Spis treści jest pusty albo nieaktualny

Uruchom pdflatex przynajmniej dwa razy:

pdflatex delta_documentation.tex
pdflatex delta_documentation.tex

Lepiej użyć:

latexmk -pdf delta_documentation.tex

23. Stare pliki pomocnicze powodują błędy

Wyczyść projekt:

latexmk -C

Następnie skompiluj ponownie:

latexmk -pdf -interaction=nonstopmode -file-line-error delta_documentation.tex

Ręcznie można usunąć między innymi:

*.aux
*.fdb_latexmk
*.fls
*.log
*.out
*.toc
*.synctex.gz

24. Diagnostyka instalacji

Linux:

which pdflatex
which latexmk
pdflatex --version
latexmk --version
kpsewhich tcolorbox.sty
kpsewhich babel-polish.tex

Windows PowerShell:

Get-Command pdflatex
Get-Command latexmk
pdflatex --version
latexmk --version
kpsewhich tcolorbox.sty
kpsewhich babel-polish.tex

Test minimalny:

\documentclass{article}
\begin{document}
Test kompilatora.
\end{document}

Zapisz jako test.tex i uruchom:

pdflatex test.tex

Jeżeli powstanie test.pdf, podstawowa instalacja działa.

Automatyzacja i CI

25. Kompilacja w GitHub Actions

Przykładowy plik .github/workflows/build-documentation.yml:

name: Build documentation PDF

on:
  push:
    paths:
      - "**.tex"
      - "**.sty"
      - "assets/**"
      - ".github/workflows/build-documentation.yml"
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Compile LaTeX
        uses: xu-cheng/latex-action@v3
        with:
          root_file: delta_documentation.tex

      - name: Upload PDF
        uses: actions/upload-artifact@v4
        with:
          name: delta-documentation
          path: delta_documentation.pdf

W środowisku produkcyjnym warto przypiąć akcje do konkretnych wersji lub skrótówcommitów zgodnie z polityką bezpieczeństwa projektu.

Zalecany sposób pracy

26. Codzienna praca

Umieść .tex, .sty i katalog assets w jednym projekcie.

Otwórz terminal w katalogu projektu.

Kompiluj:

latexmk -pdf -interaction=nonstopmode -file-line-error delta_documentation.tex

Otwórz wygenerowany PDF.

Przed zatwierdzeniem zmian usuń pliki pomocnicze:

latexmk -c

Do repozytorium dodawaj:

pliki .tex;

pliki .sty;

obrazy z katalogu assets;

opcjonalnie finalny PDF.

Przykładowy .gitignore:

*.aux
*.bbl
*.bcf
*.blg
*.fdb_latexmk
*.fls
*.log
*.out
*.run.xml
*.synctex.gz
*.toc

Jeżeli PDF nie ma być przechowywany w repozytorium:

*.pdf

Nie dodawaj tej reguły, gdy w projekcie znajdują się logotypy lub ilustracjezapisane jako PDF. W takim przypadku ignoruj tylko konkretny plik wynikowy:

/delta_documentation.pdf

Oficjalna dokumentacja

TeX Live — szybka instalacja:https://tug.org/texlive/quickinstall.html

TeX Live — instalator sieciowy:https://tug.org/texlive/acquire-netinstall.html

TeX Live — instrukcja install-tl:https://tug.org/texlive/doc/install-tl.html

TeX Live Manager:https://tug.org/texlive/doc/tlmgr.html

MiKTeX — pobieranie:https://miktex.org/download

MiKTeX — instalacja na Windowsie:https://miktex.org/howto/install-miktex

MiKTeX — automatyczna instalacja pakietów:https://docs.miktex.org/manual/autoinstall.html

Skrócona wersja

Ubuntu/Debian

sudo apt update
sudo apt install texlive-full latexmk
latexmk -pdf delta_documentation.tex

Fedora

sudo dnf install texlive-scheme-full latexmk
latexmk -pdf delta_documentation.tex

Arch Linux

sudo pacman -S texlive-meta texlive-langpolish latexmk
latexmk -pdf delta_documentation.tex

Windows z MiKTeX

Zainstaluj MiKTeX.

Zaktualizuj pakiety w MiKTeX Console.

Zainstaluj latexmk.

Zainstaluj Strawberry Perl.

Uruchom:

latexmk -pdf delta_documentation.tex

Windows z TeX Live

Zainstaluj pełny TeX Live.

Otwórz nowy PowerShell.

Uruchom:

latexmk -pdf delta_documentation.tex
