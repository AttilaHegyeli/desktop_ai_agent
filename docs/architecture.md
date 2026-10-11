# Multimodális AI Desktop Ágens – Rendszerarchitektúra és Dokumentáció

Ez a dokumentum a multimodális (hang, látás, gesztusok) asztali AI ágens adatfolyamát és technológiai felépítését részletezi a fő végrehajtási lánc alapján: `Input -> Orchestrator -> Core AI -> Action Layer -> Output`.

---

## 1. Input (Bemeneti Réteg)
A bemeneti réteg felelős a fizikai világ és a felhasználói szándék adatainak begyűjtéséért. A szenzorok párhuzamosan, külön szálakon (thread/process) futnak, hogy a folyamatos figyelés ne blokkolja a rendszert.

*   **Hang (Voice):** Folyamatosan figyeli a mikrofont. Amikor parancsot észlel, szöveggé alakítja azt (Speech-to-Text).
    *   *Technológia:* `faster-whisper` (lokális feldolgozás, alacsony késleltetés).
*   **Látás és Gesztusok (Vision & Gestures):** A kamera képét és a képernyő tartalmát dolgozza fel valós időben. 
    *   *Kézkövetés:* Kiszámítja az ujjak térbeli 3D koordinátáit és azonosítja a gesztusokat (pl. csippentés, mutatás).
    *   *Képernyőolvasás:* Rögzíti az aktuális képernyőállapotot (screenshot), ha az ágensnek vizuális kontextusra van szüksége.
    *   *Technológia:* `OpenCV`, `MediaPipe Hands` (gesztusokhoz), `mss` (gyors képernyőfotóhoz).

## 2. Orchestrator (Központi Irányító)
Az Orchestrator a rendszer "diszpécsere". Nem hoz önálló logikai döntéseket és nem ír kódot, feladata a bemenetek szinkronizációja, a zajszűrés és a kontextusépítés.

*   **Szinkronizáció:** Összekapcsolja a párhuzamosan érkező adatokat. Ha a felhasználó a mikrofonba mondja, hogy *"Kattints ide"*, az Orchestrator megvárja és párosítja ezt a gesztusfelismerő modultól érkező pontos X-Y képernyőkoordinátával.
*   **Zajszűrés:** Megakadályozza, hogy a folyamatos 60 FPS kamerakép vagy a háttérzaj túlterhelje az AI magot. Csak releváns, befejezett eseményeket (triggereket) továbbít.
*   **Prompt generálás:** A szinkronizált adatokból egyetlen, jól strukturált, gépileg értelmezhető JSON vagy szöveges csomagot (promptot) készít a Core AI számára (pl. *"Felhasználói kérés: 'Nyisd meg a VS Code-ot'. Aktuális kurzor pozíció: X: 1024, Y: 768."*).
*   *Technológia:* Python `asyncio` / `multiprocessing`, kommunikációhoz `ZeroMQ` vagy beépített Queues.

## 3. Core AI (Központi Intelligencia)
A rendszer logikai agya. Fogadja az Orchestrator által előkészített kontextust, értelmezi a felhasználó szándékát, és konkrét, végrehajtható programkódot (szkriptet) generál.

*   **Szándékfelismerés:** Megérti az összetett kéréseket és lépésekre bontja azokat (Chain-of-Thought).
*   **Kódgenerálás:** A kért akcióhoz operációs rendszer specifikus parancsokat vagy Python szoftverautomatizációs szkripteket ír (pl. egy `pyautogui` hívást az egér mozgatására, vagy egy `subprocess` hívást egy alkalmazás megnyitására).
*   *Technológia:* Nagy Nyelvi Modell (LLM) letisztult eszközhasználati (Tool-Calling) képességekkel. 
    *   *Lokális:* `Llama-3.1-8B` vagy `Qwen2.5-Coder` (Ollama keretrendszerben).
    *   *Felhős (Gyorsabb):* `Llama-3.1-70B` a Groq API-n keresztül.

## 4. Action Layer (Végrehajtó Környezet)
Ez a réteg hidat képez a Core AI által generált szöveges kód és a tényleges operációs rendszer között. Felelős a szkriptek futtatásáért és a hibakezelésért.

*   **Futtatás:** Átveszi a Core AI által írt Python/Bash/PowerShell kódot, és biztonságosan végrehajtja a terminálban vagy egy izolált környezetben.
*   **Hibajavító Hurok (Self-Correction):** Ha a kód lefutása közben hiba történik (pl. egy fájl nem található), az Action Layer visszaküldi a hibaüzenetet a Core AI-nak, amely azonnal javítja a kódot, és a folyamat újraindul.
*   *Technológia:* `Open Interpreter` (Nyílt forráskódú keretrendszer az LLM-ek által generált kódok biztonságos, lokális futtatására).

## 5. Output (Kimenet)
A végrehajtási lánc fizikai és vizuális eredménye az operációs rendszerben. 

*   **OS Interakciók:** A kurzor tényleges elmozdulása a képernyőn, alkalmazások megnyílása, ablakok átméretezése, vagy szöveg automatikus begépelése a célmezőbe.
*   **Visszacsatolás (Feedback):** Hangalapú nyugtázás (Text-to-Speech) vagy a felületen (HUD/Overlay) megjelenő vizuális indikátor arról, hogy a feladat sikeresen befejeződött.
*   *Technológia:* `PyAutoGUI`, `pygetwindow`, `os/subprocess` modulok (amit az Action Layer aktivál), frontend overlay-hez `PySide6`.