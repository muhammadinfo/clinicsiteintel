"""ClinicSiteIntel — desktop application entry point.

A Windows-installable, interactive clinic-site assessment tool: type an
address, get a live demographic + competitor + referral + real-estate +
statistical report, built around the same LVI methodology validated in
the market-intelligence.html dashboard for Advanced Dental Sleep & TMJ
Clinic / Facial Pain LLC.
"""
import sys
import traceback

from PySide6.QtCore import Qt, QThread, Signal, QMarginsF
from PySide6.QtGui import QFont, QColor, QPixmap, QPageLayout, QPageSize
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTabWidget, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QListWidget, QListWidgetItem,
    QSplitter, QPlainTextEdit, QFormLayout, QGroupBox, QProgressBar,
    QScrollArea, QFrame, QSpinBox, QFileDialog, QComboBox,
)

import config
import db
import compare
import report as report_mod
import realestate
import nppes
import sitescout

# ---- iOS-style light palette (also used by the inline-HTML report renderers) ----
PAL = {
    "bg": "#f2f2f7", "surface": "#ffffff", "surface2": "#f9f9fb",
    "border": "#e5e5ea", "sep": "#d1d1d6",
    "text": "#1c1c1e", "text2": "#6e6e73", "text3": "#aeaeb2",
    "blue": "#007aff", "green": "#34c759", "amber": "#ff9500",
    "red": "#ff3b30", "indigo": "#5856d6", "teal": "#30b0c7",
}

LIGHT_STYLE = f"""
QWidget {{ background-color: {PAL['bg']}; color: {PAL['text']};
    font-family: 'SF Pro Text','Helvetica Neue','Segoe UI',system-ui,sans-serif; font-size: 13px; }}
QMainWindow {{ background-color: {PAL['bg']}; }}
QLineEdit, QPlainTextEdit, QTextEdit {{ background-color: {PAL['surface']}; border: 1px solid {PAL['border']};
    border-radius: 10px; padding: 10px 13px; color: {PAL['text']}; selection-background-color: #b3d7ff; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border: 2px solid {PAL['blue']}; padding: 9px 12px; }}
QPushButton {{ background-color: {PAL['blue']}; border: none; border-radius: 10px; padding: 11px 22px;
    color: #ffffff; font-weight: 600; font-size: 14px; }}
QPushButton:hover {{ background-color: #0a84ff; }}
QPushButton:pressed {{ background-color: #006fe0; }}
QPushButton:disabled {{ background-color: #c7c7cc; color: #ffffff; }}
QTabWidget::pane {{ border: none; top: 0; background: transparent; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; padding: 9px 16px; margin-right: 2px; color: {PAL['text2']};
    border: none; border-radius: 8px; font-weight: 600; }}
QTabBar::tab:selected {{ color: #ffffff; background: {PAL['blue']}; }}
QTabBar::tab:hover:!selected {{ background: #e1e1e6; color: {PAL['text']}; }}
QTableWidget {{ background-color: {PAL['surface']}; gridline-color: {PAL['border']}; border: 1px solid {PAL['border']};
    border-radius: 12px; alternate-background-color: {PAL['surface2']}; }}
QTableWidget::item {{ padding: 5px 7px; }}
QTableWidget::item:selected {{ background-color: #d9ecff; color: {PAL['text']}; }}
QHeaderView::section {{ background-color: {PAL['surface2']}; color: {PAL['text2']}; padding: 8px 7px;
    border: none; border-bottom: 1px solid {PAL['sep']}; font-weight: 700; font-size: 11px; letter-spacing: 0.4px; }}
QGroupBox {{ background: {PAL['surface']}; border: 1px solid {PAL['border']}; border-radius: 12px;
    margin-top: 14px; padding-top: 18px; font-weight: 700; color: {PAL['text']}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; }}
QListWidget {{ background-color: {PAL['surface']}; border: 1px solid {PAL['border']}; border-radius: 12px; }}
QListWidget::item {{ padding: 9px 10px; border-bottom: 1px solid {PAL['border']}; color: {PAL['text']}; }}
QListWidget::item:hover {{ background-color: {PAL['surface2']}; }}
QProgressBar {{ border: none; border-radius: 3px; background: {PAL['border']}; text-align: center; max-height: 6px; }}
QProgressBar::chunk {{ background-color: {PAL['blue']}; border-radius: 3px; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #c7c7cc; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #b0b0b5; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QMessageBox {{ background-color: {PAL['surface']}; }}
QLabel {{ background: transparent; }}
QLabel#heading {{ font-size: 24px; font-weight: 800; color: {PAL['text']}; letter-spacing: -0.3px; }}
QLabel#subheading {{ color: {PAL['text2']}; font-size: 12px; }}
QLabel#progress {{ color: {PAL['blue']}; font-weight: 600; }}
"""


class _SortItem(QTableWidgetItem):
    """Table cell that sorts NUMERICALLY when both values are numbers (so a
    'Dist (mi)' or 'Score' column sorts 5.4 < 11.6 rather than as text), and
    falls back to case-insensitive string compare otherwise."""
    def __lt__(self, other):
        def num(t):
            try:
                return float(str(t).replace(",", "").replace("$", "").replace("mi", "").strip())
            except (ValueError, AttributeError):
                return None
        a, b = num(self.text()), num(other.text())
        if a is not None and b is not None:
            return a < b
        return self.text().lower() < other.text().lower()


class ReportWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(dict)
    finished_err = Signal(str)

    def __init__(self, address, google_key, census_key, known_competitors=None):
        super().__init__()
        self.address = address
        self.google_key = google_key
        self.census_key = census_key
        self.known_competitors = known_competitors or []

    def run(self):
        try:
            result = report_mod.run_full_report(
                self.address, self.google_key, self.census_key,
                progress_cb=lambda m: self.progress.emit(m),
                known_competitors=self.known_competitors,
            )
            self.finished_ok.emit(result)
        except Exception as e:
            self.finished_err.emit(f"{e}\n\n{traceback.format_exc()}")


class _CompareAIWorker(QThread):
    """Runs the (slow, blocking) Anthropic comparative assessment off the UI thread."""
    done = Signal(str)

    def __init__(self, key, repA, repB):
        super().__init__()
        self.key, self.repA, self.repB = key, repA, repB

    def run(self):
        try:
            html = compare.build_ai_insight(self.key, self.repA, self.repB)
        except Exception as e:
            html = f"<p style='color:#ff3b30;'>AI assessment failed: {e.__class__.__name__}</p>"
        self.done.emit(html)


class ListingsWorker(QThread):
    """Fetches recent listings for a ZIP and scores each with the verdict engine,
    off the UI thread. Emits (listing_dict, verdict_dict) per analyzed listing."""
    progress = Signal(str)
    one_done = Signal(dict, dict)
    finished_all = Signal(int)
    finished_err = Signal(str)

    def __init__(self, zip_code, radius, cfg, preset_listings=None, ctx_cache=None,
                 preset_records=None):
        super().__init__()
        self.zip_code = zip_code
        self.radius = radius
        self.cfg = cfg
        self.preset_listings = preset_listings   # already-geocoded listings
        self.preset_records = preset_records     # raw records — geocoded in this thread
        self.ctx_cache = ctx_cache if ctx_cache is not None else {}  # shared, persists across imports

    def run(self):
        try:
            from dataclasses import asdict
            if self.preset_records is not None:
                # Geocode the pasted batch HERE, off the UI thread, so a large
                # import (100+ addresses, each a geocoder round-trip) never
                # freezes the window.
                self.progress.emit(f"Geocoding {len(self.preset_records)} listing(s)…")
                listings = sitescout.listings_from_records(
                    self.preset_records, log=lambda m: self.progress.emit(m))
                self.progress.emit(f"Analyzing {len(listings)} imported listing(s)…")
            elif self.preset_listings is not None:
                listings = self.preset_listings
                self.progress.emit(f"Analyzing {len(listings)} imported listing(s)…")
            else:
                self.progress.emit("Fetching recent listings (≤60 days on market)…")
                listings = sitescout.fetch_recent_listings(
                    self.zip_code, self.radius, self.cfg.get("apify_key", ""),
                    log=lambda m: self.progress.emit(m))
            state = "CA"
            kc = self.cfg.get("known_competitors", [])
            census = self.cfg.get("census_key", "") or self.cfg.get("census_api_key", "")
            mapbox = self.cfg.get("mapbox_key", "")
            ctx_cache = self.ctx_cache   # shared ZIP -> market context (persists across imports)
            for i, pl in enumerate(listings):
                lz = _zip5(pl.address) or self.zip_code
                ctx = ctx_cache.get(lz)
                if ctx is None:
                    self.progress.emit(f"Building market context for ZIP {lz} "
                                       f"({i+1}/{len(listings)})…")
                    ctx = sitescout.build_context(pl, lz, state, kc, census,
                                                  log=lambda m: self.progress.emit(m))
                    ctx_cache[lz] = ctx
                self.progress.emit(f"Scoring {i+1}/{len(listings)}: {pl.address}")
                v = sitescout.calculate_location_verdict(pl, ctx, mapbox)
                if self.cfg.get("anthropic_key"):
                    cv = sitescout.claude_vision_analyze(
                        self.cfg["anthropic_key"], lz,
                        f"{pl.address}\n{v.reasoning_simple}\n{v.medical_dental_fit}")
                    if cv:
                        v.verdict = cv.get("verdict", v.verdict)
                        v.reasoning_simple = cv.get("reasoning_simple", v.reasoning_simple)
                self.one_done.emit(asdict(pl), asdict(v))
            self.finished_all.emit(len(listings))
        except Exception as e:
            self.finished_err.emit(f"{e}\n\n{traceback.format_exc()}")


def _zip5(text):
    # The ZIP is the LAST 5-digit group in a US address — taking the first one
    # wrongly grabs a 5-digit street number (e.g. "21550 Oxnard St … 91367").
    import re as _re
    m = _re.findall(r"\b\d{5}\b", text or "")
    return m[-1] if m else None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ClinicSiteIntel — Clinic Site Assessment")
        self.resize(1280, 860)
        self.cfg = config.load_config()
        self.current_report = None
        self.worker = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        header_frame = QWidget()
        header_frame.setObjectName("headerframe")
        header_frame.setStyleSheet(
            "#headerframe { background: #ffffff; border: 1px solid #e5e5ea; "
            "border-radius: 14px; }"
        )
        header = QHBoxLayout(header_frame)
        header.setContentsMargins(16, 12, 16, 12)

        crest = QLabel()
        crest_path = nppes.resource_path("assets", "crest.gif")
        pix = QPixmap(crest_path)
        if not pix.isNull():
            crest.setPixmap(pix.scaled(54, 54, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            header.addWidget(crest)
            header.addSpacing(12)

        title = QLabel("ClinicSiteIntel")
        title.setObjectName("heading")
        sub = QLabel("Live address-based clinic market & demographic assessment")
        sub.setObjectName("subheading")
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_box.addWidget(title)
        title_box.addWidget(sub)
        header.addLayout(title_box)
        header.addStretch()

        tagline = QLabel("Advanced Dental Sleep & TMJ Clinic")
        tagline.setStyleSheet("color: #8e8e93; font-size: 11px;")
        header.addWidget(tagline)
        layout.addWidget(header_frame)

        search_row = QHBoxLayout()
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("Enter a full address, e.g. 2220 Lynn Rd, Thousand Oaks, CA 91360")
        self.run_btn = QPushButton("Run Report")
        self.run_btn.clicked.connect(self.on_run_report)
        self.export_pdf_btn = QPushButton("⬇ Export PDF")
        self.export_pdf_btn.setEnabled(False)
        self.export_pdf_btn.clicked.connect(self.on_export_pdf)
        search_row.addWidget(self.address_input, 4)
        search_row.addWidget(self.run_btn, 1)
        search_row.addWidget(self.export_pdf_btn, 1)
        layout.addLayout(search_row)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("progress")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.summary_tab = QWebEngineView()   # premium HTML/CSS consultant summary
        self.tabs.addTab(self.summary_tab, "Summary")

        self.demo_tab = QTextEdit(readOnly=True)
        self.tabs.addTab(self.demo_tab, "Demographics")

        self.competitors_tab = QWidget()
        self._build_competitors_tab()
        self.tabs.addTab(self.competitors_tab, "Competitors")

        self.referrals_tab = QWidget()
        self._build_referrals_tab()
        self.tabs.addTab(self.referrals_tab, "Referral Map")

        self.realestate_tab = QWidget()
        self._build_realestate_tab()
        self.tabs.addTab(self.realestate_tab, "Real Estate")

        self.sitescout_tab = QWidget()
        self._build_sitescout_tab()
        self.tabs.addTab(self.sitescout_tab, "Site Scout")

        self.compare_tab = QWidget()
        self._build_compare_tab()
        self.tabs.addTab(self.compare_tab, "Compare")

        self.stats_tab = QTextEdit(readOnly=True)
        self.tabs.addTab(self.stats_tab, "Statistics")

        self.saved_tab = QWidget()
        self._build_saved_tab()
        self.tabs.addTab(self.saved_tab, "Saved Reports")

        self.settings_tab = QWidget()
        self._build_settings_tab()
        self.tabs.addTab(self.settings_tab, "Settings")

    # ---------------- Competitors / Referral tabs ----------------
    def _build_competitors_tab(self):
        v = QVBoxLayout(self.competitors_tab)
        self.competitors_status = QLabel("")
        self.competitors_status.setWordWrap(True)
        self.competitors_status.setStyleSheet(
            "background:#eef5ff; border:1px solid #d6e6ff; border-left:3px solid #007aff; "
            "border-radius:10px; padding:11px 14px; color:#1c4e8a; font-size:12px;"
        )
        self.competitors_table = QTableWidget()
        self._style_table(self.competitors_table)
        v.addWidget(self.competitors_status)
        v.addWidget(self.competitors_table)
        self._set_competitors_status_idle()

    @staticmethod
    def _style_table(table):
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setWordWrap(False)
        table.verticalHeader().setDefaultSectionSize(30)
        table.setSortingEnabled(True)                 # click any header to sort
        table.horizontalHeader().setSortIndicatorShown(True)
        table.horizontalHeader().setSectionsClickable(True)

    def _set_competitors_status_idle(self):
        self.competitors_status.setText(
            "No report run yet. COMPETITORS = dentists who do orofacial pain / TMJ / dental sleep: "
            "credentialed orofacial-pain specialists (NPI Registry) + general dentists whose website "
            "advertises those services. Free, no API key needed."
        )
        self.competitors_status.show()

    def _build_referrals_tab(self):
        v = QVBoxLayout(self.referrals_tab)
        self.referrals_status = QLabel("")
        self.referrals_status.setWordWrap(True)
        self.referrals_status.setStyleSheet(
            "background:#eef5ff; border:1px solid #d6e6ff; border-left:3px solid #007aff; "
            "border-radius:10px; padding:11px 14px; color:#1c4e8a; font-size:12px;"
        )
        self.referrals_table = QTableWidget()
        self._style_table(self.referrals_table)
        v.addWidget(self.referrals_status)
        v.addWidget(self.referrals_table)
        self._set_referrals_status_idle()

    def _set_referrals_status_idle(self):
        self.referrals_status.setText(
            "No report run yet. REFERRAL SOURCES = physicians of all specialties (MD/DO, via the NPI "
            "Registry) + general dentists who do NOT advertise TMJ/sleep and could refer those cases "
            "to you. Free, no API key needed."
        )
        self.referrals_status.show()

    # ---------------- Real Estate tab ----------------
    # ---------------- Site Scout tab (listings + verdict engine) ----------------
    def _build_sitescout_tab(self):
        self._ss_cards = {}           # listing id -> (card_widget, body_widget)
        self._ss_rows = []            # (listing_dict, verdict_dict) for export
        v = QVBoxLayout(self.sitescout_tab)

        ctrl = QHBoxLayout()
        self.ss_zip = QLineEdit(self.cfg.get("zip", "91360"))
        self.ss_zip.setPlaceholderText("ZIP")
        self.ss_zip.setMaximumWidth(90)
        self.ss_radius = QSpinBox()
        self.ss_radius.setRange(3, 30)
        self.ss_radius.setValue(int(self.cfg.get("radius", 10)))
        self.ss_radius.setSuffix(" mi")
        self.ss_search_btn = QPushButton("🔎 Search & analyze recent listings")
        self.ss_search_btn.clicked.connect(self.on_sitescout_search)
        self.ss_export_btn = QPushButton("⬇ Export shortlist (CSV)")
        self.ss_export_btn.clicked.connect(self.on_sitescout_export)
        ctrl.addWidget(QLabel("ZIP")); ctrl.addWidget(self.ss_zip)
        ctrl.addWidget(QLabel("Radius")); ctrl.addWidget(self.ss_radius)
        ctrl.addWidget(self.ss_search_btn)
        ctrl.addStretch()
        ctrl.addWidget(self.ss_export_btn)
        v.addLayout(ctrl)

        bulk_group = QGroupBox("Import scouted listings  —  one per line:  address | price/yr | sqft | source")
        bulk_layout = QVBoxLayout(bulk_group)
        self.ss_bulk = QPlainTextEdit()
        self.ss_bulk.setPlaceholderText(
            "Paste listings found in your browser (LoopNet/Crexi) — or I'll read them off your "
            "screen and fill this in. Example:\n"
            "2220 Lynn Rd, Thousand Oaks CA 91360 | 46200 | 1100 | LoopNet\n"
            "179 Auburn Ct, Westlake Village CA 91362 | 32400 | 1800 | Crexi")
        self.ss_bulk.setMaximumHeight(96)
        bulk_btns = QHBoxLayout()
        self.ss_bulk_btn = QPushButton("📥 Import & analyze (adds to results; skips already-done)")
        self.ss_bulk_btn.clicked.connect(self.on_sitescout_bulk_import)
        self.ss_clear_btn = QPushButton("🗑 Clear results")
        self.ss_clear_btn.clicked.connect(self._reset_sitescout)
        bulk_btns.addWidget(self.ss_bulk_btn, 1)
        bulk_btns.addWidget(self.ss_clear_btn)
        bulk_layout.addWidget(self.ss_bulk)
        bulk_layout.addLayout(bulk_btns)
        v.addWidget(bulk_group)

        # Persistent across imports so a 200-listing batch can be built up in
        # chunks without re-analyzing or rebuilding shared ZIP market context.
        self._ss_ctx_cache = {}       # ZIP -> market context
        self._ss_done = set()         # normalized address -> already analyzed

        self.ss_status = QLabel("Recent medical/dental listings get a Strong Buy / Viable / "
                                "Caution / Not Recommended verdict. (Sample feed unless an Apify key is set.)")
        self.ss_status.setWordWrap(True)
        self.ss_status.setStyleSheet("color:#6e6e73; font-size:12px;")
        v.addWidget(self.ss_status)

        # Best-to-worst ranked summary of every analyzed listing.
        self.ss_ranking = QTextEdit(readOnly=True)
        self.ss_ranking.setStyleSheet("border:1px solid #e5e5ea; border-radius:10px; background:#fff;")
        self.ss_ranking.setMaximumHeight(230)
        self.ss_ranking.hide()
        v.addWidget(self.ss_ranking)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.ss_cards_host = QWidget()
        self.ss_cards_layout = QVBoxLayout(self.ss_cards_host)
        self.ss_cards_layout.addStretch()
        scroll.setWidget(self.ss_cards_host)
        v.addWidget(scroll, 1)

    def _verdict_color(self, verdict):
        return {"Strong Buy": "#34c759", "Viable": "#ff9f0a",
                "Caution": "#ff9500", "Not Recommended": "#ff3b30"}.get(verdict, "#8e8e93")

    def _add_verdict_card(self, pl: dict, v: dict):
        color = self._verdict_color(v["verdict"])
        card = QWidget()
        card.setStyleSheet("background:#ffffff; border:1px solid #e5e5ea; border-radius:12px;")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 0, 0, 0)

        price = f"${pl['price']:,.0f}/yr" if pl.get("price") else "Price n/a"
        sqft = f"{pl['sqft']:,.0f} SF" if pl.get("sqft") else "SF n/a"
        header = QPushButton(f"   {pl['address']}      {price} · {sqft}      ▸")
        header.setCheckable(True)
        header.setStyleSheet(
            "QPushButton{text-align:left; padding:12px 14px; border:none; background:transparent;"
            "color:#1c1c1e; font-weight:600; font-size:13px;} QPushButton:hover{background:#f2f2f7;}")
        # Per-listing "Full report" action: push this address through the main
        # report engine and view its Summary / Competitors / Referrals tabs.
        open_btn = QPushButton("Full report ↗")
        open_btn.setToolTip("Run the full demographic, competitor and referral "
                            "report for this address and open it in the Summary tab.")
        open_btn.setStyleSheet(
            "QPushButton{background:#eef1ff; color:#1c1c1e; border:1px solid #d6d6e0;"
            "border-radius:10px; padding:5px 12px; font-size:11px; font-weight:600;}"
            "QPushButton:hover{background:#e1e6ff;}")
        _addr = pl["address"]
        open_btn.clicked.connect(lambda _=False, a=_addr: self._open_full_report_for(a))

        badge = QLabel(f"  {v['verdict']} · {v['score']}  ")
        badge.setStyleSheet(f"background:{color}; color:#fff; font-weight:800; border-radius:12px; padding:3px 0;")
        badge.setFixedWidth(150)
        hb = QHBoxLayout()
        hb.setContentsMargins(0, 0, 10, 0)
        hb.addWidget(header, 1)
        hb.addWidget(open_btn)
        hb.addWidget(badge)
        cl.addLayout(hb)

        body = QTextEdit(readOnly=True)
        body.setStyleSheet("border:none; border-top:1px solid #e5e5ea; background:#fff;")
        body.setHtml(self._verdict_card_html(pl, v))
        body.setMinimumHeight(360)
        body.hide()
        cl.addWidget(body)

        def toggle(checked):
            body.setVisible(checked)
            header.setText(header.text()[:-1] + ("▾" if checked else "▸"))
        header.toggled.connect(toggle)

        self.ss_cards_layout.insertWidget(self.ss_cards_layout.count() - 1, card)
        self._ss_cards[pl["id"]] = card

    def _open_full_report_for(self, address):
        """From a Site Scout verdict card, run the full report engine for this
        one address and surface it in the main Summary / Competitors / Referral
        tabs — bridging the lightweight scout score to the deep assessment."""
        if getattr(self, "worker", None) is not None and self.worker.isRunning():
            QMessageBox.information(self, "Report in progress",
                                    "A full report is already running — let it finish first.")
            return
        self.address_input.setText(address)
        self.tabs.setCurrentIndex(0)   # Summary
        self.on_run_report()

    def _verdict_card_html(self, pl, v):
        P = PAL
        refs = "".join(f"<li>{n} · {s} · {m} mi</li>" for n, s, m in v.get("referrals_near", [])) or "<li>—</li>"
        comps = "".join(f"<li>{n} · {t} · {m} mi</li>" for n, t, m in v.get("competitors_near", [])) or "<li>—</li>"
        risks = "".join(f"<li>{r}</li>" for r in v.get("risks", []))
        opps = "".join(f"<li>{o}</li>" for o in v.get("opportunities", []))
        dt = (f" · nearest competitor {v['drive_time_minutes']} min drive"
              if v.get("drive_time_minutes") else "")
        return f"""
        <div style="font-family:'Segoe UI'; color:{P['text']}; font-size:12.5px;">
          <p style="color:{P['blue']}; font-weight:700;">Capture {v['capture_score']}%{dt}</p>
          <h4 style="margin:6px 0 2px;">🧭 Verdict reasoning</h4><p>{v['reasoning_simple']}</p>
          <h4 style="margin:8px 0 2px;">🚗 Drive-time isochrones</h4><p>{v['isochrone_summary']}</p>
          <h4 style="margin:8px 0 2px;">🦷 Medical / dental fit</h4><p>{v['medical_dental_fit']}</p>
          <h4 style="margin:8px 0 2px;">💳 Payer & affluence</h4><p>{v['payer_affluence_summary']}</p>
          <table width="100%"><tr valign="top">
            <td width="50%"><h4 style="margin:8px 0 2px;">🤝 Referral proximity</h4>
                <ul style="margin:0; padding-left:16px; color:{P['text2']};">{refs}</ul></td>
            <td width="50%"><h4 style="margin:8px 0 2px;">⚔️ Competitor density (10 mi)</h4>
                <ul style="margin:0; padding-left:16px; color:{P['text2']};">{comps}</ul></td>
          </tr></table>
          <table width="100%"><tr valign="top">
            <td width="50%"><h4 style="margin:8px 0 2px; color:{P['green']};">Opportunities</h4>
                <ul style="margin:0; padding-left:16px;">{opps}</ul></td>
            <td width="50%"><h4 style="margin:8px 0 2px; color:{P['red']};">Risks</h4>
                <ul style="margin:0; padding-left:16px;">{risks}</ul></td>
          </tr></table>
          <p style="margin-top:8px; padding:8px 10px; background:{P['surface2']}; border-radius:8px;">
             <b>Recommendation:</b> {v['recommendation']}</p>
        </div>"""

    def _clear_sitescout_cards(self):
        for cid, card in list(self._ss_cards.items()):
            card.setParent(None)
        self._ss_cards.clear()
        self._ss_rows.clear()

    def _reset_sitescout(self):
        self._clear_sitescout_cards()
        self._ss_ctx_cache.clear()
        self._ss_done.clear()
        self.ss_ranking.hide()
        self.ss_status.setText("Cleared. Paste a batch and Import to start fresh.")

    @staticmethod
    def _addr_key(addr):
        return "".join(ch for ch in (addr or "").lower() if ch.isalnum())

    def _ss_busy(self, busy):
        self.ss_search_btn.setEnabled(not busy)
        self.ss_bulk_btn.setEnabled(not busy)
        self.ss_clear_btn.setEnabled(not busy)

    def _start_ss_worker(self, preset_listings=None, preset_records=None):
        cfg = dict(self.cfg)
        cfg["census_key"] = self.cfg.get("census_api_key", "")
        self._ss_busy(True)
        self._ss_worker = ListingsWorker(self.ss_zip.text().strip(), self.ss_radius.value(),
                                         cfg, preset_listings=preset_listings,
                                         ctx_cache=self._ss_ctx_cache,
                                         preset_records=preset_records)
        self._ss_worker.progress.connect(lambda m: self.ss_status.setText(m))
        self._ss_worker.one_done.connect(self._on_sitescout_one)
        self._ss_worker.finished_all.connect(self._on_sitescout_done)
        self._ss_worker.finished_err.connect(
            lambda msg: (self._ss_busy(False), self.ss_status.setText("Failed."),
                         QMessageBox.critical(self, "Site Scout failed", msg)))
        self._ss_worker.start()

    def on_sitescout_bulk_import(self):
        recs = sitescout.parse_bulk_lines(self.ss_bulk.toPlainText())
        if not recs:
            self.ss_status.setText("Paste at least one listing line (address | price | sqft | source).")
            return
        # Skip anything already analyzed BEFORE geocoding — the dedup key is the
        # raw address text, so no geocoder round-trip is needed to filter. This
        # lets a 200-item batch grow in chunks without re-geocoding done rows.
        recs = [r for r in recs if self._addr_key(r.get("address")) not in self._ss_done]
        if not recs:
            self.ss_status.setText("All pasted listings are already analyzed. "
                                   "Use Clear results to start over.")
            return
        # Geocoding now happens inside the worker thread, so importing a large
        # batch no longer freezes the UI ("Not Responding").
        self.ss_status.setText(f"Queued {len(recs)} new listing(s) — geocoding in background…")
        self._start_ss_worker(preset_records=recs)

    def on_sitescout_search(self):
        self._reset_sitescout()
        self._start_ss_worker(None)

    def _on_sitescout_one(self, pl, v):
        key = self._addr_key(pl.get("address"))
        if key in self._ss_done:
            return
        self._ss_done.add(key)
        self._add_verdict_card(pl, v)
        self._ss_rows.append((pl, v))
        self._rerank_sitescout()

    def _rerank_sitescout(self):
        """Build a best-to-worst ranked summary of every analyzed listing."""
        if not self._ss_rows:
            self.ss_ranking.hide()
            return
        ranked = sorted(self._ss_rows, key=lambda r: -(r[1].get("score") or 0))
        rows = ""
        for i, (pl, v) in enumerate(ranked, 1):
            color = self._verdict_color(v["verdict"])
            price = f"${pl['price']:,.0f}/yr" if pl.get("price") else "—"
            cap = v.get("capture_score")
            cap_s = f"{cap}%" if cap is not None else "—"
            rows += (
                f"<tr style='border-bottom:1px solid #f0f0f3;'>"
                f"<td style='padding:5px 8px;font-weight:800;color:#8e8e93;'>{i}</td>"
                f"<td style='padding:5px 8px;color:#1c1c1e;'>{pl.get('address','')}</td>"
                f"<td style='padding:5px 8px;color:#6e6e73;'>{price}</td>"
                f"<td style='padding:5px 8px;text-align:center;'>"
                f"<b style='color:{color};'>{v['verdict']}</b></td>"
                f"<td style='padding:5px 8px;text-align:right;font-weight:800;color:{color};'>{v['score']}</td>"
                f"<td style='padding:5px 8px;text-align:right;color:#6e6e73;'>{cap_s}</td></tr>")
        html = (
            "<div style='font-family:Segoe UI;'>"
            "<div style='font-weight:800;font-size:13px;color:#1c1c1e;padding:2px 4px 8px;'>"
            f"Ranked best → worst &nbsp;<span style='color:#8e8e93;font-weight:600;'>"
            f"({len(ranked)} listings)</span></div>"
            "<table style='width:100%;border-collapse:collapse;font-size:12px;'>"
            "<tr style='color:#8e8e93;font-size:10px;letter-spacing:.04em;'>"
            "<th style='text-align:left;padding:0 8px;'>#</th>"
            "<th style='text-align:left;padding:0 8px;'>ADDRESS</th>"
            "<th style='text-align:left;padding:0 8px;'>PRICE</th>"
            "<th style='text-align:center;padding:0 8px;'>VERDICT</th>"
            "<th style='text-align:right;padding:0 8px;'>SCORE</th>"
            "<th style='text-align:right;padding:0 8px;'>CAPTURE</th></tr>"
            f"{rows}</table></div>")
        self.ss_ranking.setHtml(html)
        self.ss_ranking.show()

    def _on_sitescout_done(self, n):
        self._ss_busy(False)
        self._rerank_sitescout()
        self.ss_status.setText(f"Done — {len(self._ss_rows)} total analyzed "
                               f"({n} this run), ranked best to worst above. "
                               "Paste more and Import to add to the batch.")

    def on_sitescout_export(self):
        if not self._ss_rows:
            self.ss_status.setText("Nothing to export yet — run a search first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export shortlist", "site_scout_shortlist.csv", "CSV (*.csv)")
        if not path:
            return
        import csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["address", "price", "sqft", "verdict", "score", "capture_%", "recommendation"])
            for pl, v in self._ss_rows:
                w.writerow([pl["address"], pl.get("price"), pl.get("sqft"), v["verdict"],
                            v["score"], v["capture_score"], v["recommendation"]])
        self.ss_status.setText(f"Exported {len(self._ss_rows)} listings to {path}")

    def _build_realestate_tab(self):
        v = QVBoxLayout(self.realestate_tab)
        links_group = QGroupBox("Direct search links (one click opens the platform's filtered search)")
        links_layout = QVBoxLayout(links_group)
        self.realestate_links_list = QListWidget()
        self.realestate_links_list.itemDoubleClicked.connect(self._open_link_item)
        links_layout.addWidget(QLabel("Double-click a link to open it in your browser."))
        links_layout.addWidget(self.realestate_links_list)
        v.addWidget(links_group)

        paste_group = QGroupBox("Paste a listing — auto-extract SF / rate / build-out / HVAC")
        paste_layout = QVBoxLayout(paste_group)
        self.paste_box = QPlainTextEdit()
        self.paste_box.setPlaceholderText("Paste the full listing text from LoopNet/Crexi/CityFeet/Carr here...")
        extract_btn = QPushButton("Extract fields")
        extract_btn.clicked.connect(self.on_extract_listing)
        self.extract_result = QTextEdit(readOnly=True)
        self.extract_result.setMaximumHeight(160)
        paste_layout.addWidget(self.paste_box)
        paste_layout.addWidget(extract_btn)
        paste_layout.addWidget(self.extract_result)
        v.addWidget(paste_group)

    def _open_link_item(self, item: QListWidgetItem):
        import webbrowser
        webbrowser.open(item.data(Qt.UserRole))

    def on_extract_listing(self):
        text = self.paste_box.toPlainText().strip()
        if not text:
            return
        result = realestate.extract_from_pasted_text(text)
        summary = (
            f"Address: {result.address or '(not detected)'}\n"
            f"Square feet: {result.square_feet or '(not detected)'}\n"
            f"Rate: {('$' + str(result.rate_per_sf_yr) + '/SF/yr') if result.rate_per_sf_yr else '(not detected)'}\n"
            f"Build-out: {result.buildout}\n"
            f"HVAC: {result.hvac}\n\n"
            f"Note: {result.confidence_note}"
        )
        self.extract_result.setPlainText(summary)
        report_id = self.current_report.get("_db_id") if self.current_report else None
        db.save_pasted_listing(report_id, text, result.__dict__)

    # ---------------- Compare tab ----------------
    def _build_compare_tab(self):
        v = QVBoxLayout(self.compare_tab)
        ctrl = QHBoxLayout()
        self.cmp_a = QComboBox(); self.cmp_b = QComboBox()
        self.cmp_a.setMinimumWidth(230); self.cmp_b.setMinimumWidth(230)
        self.cmp_run_btn = QPushButton("⚖  Compare")
        self.cmp_run_btn.clicked.connect(self.on_compare)
        self.cmp_ai_btn = QPushButton("🧠  Generate AI assessment")
        self.cmp_ai_btn.clicked.connect(self.on_compare_ai)
        self.cmp_ai_btn.setEnabled(False)
        ctrl.addWidget(QLabel("Site A")); ctrl.addWidget(self.cmp_a, 1)
        ctrl.addWidget(QLabel("vs")); ctrl.addWidget(QLabel("Site B")); ctrl.addWidget(self.cmp_b, 1)
        ctrl.addWidget(self.cmp_run_btn); ctrl.addWidget(self.cmp_ai_btn)
        v.addLayout(ctrl)
        self.cmp_status = QLabel("Pick two analyzed addresses and click Compare. Run reports in the "
                                 "Summary tab first — they appear here automatically.")
        self.cmp_status.setWordWrap(True)
        self.cmp_status.setStyleSheet("color:#6e6e73; font-size:12px;")
        v.addWidget(self.cmp_status)
        self.compare_view = QWebEngineView()
        v.addWidget(self.compare_view, 1)
        self._cmp_repA = self._cmp_repB = None
        self._refresh_compare_choices()

    def _refresh_compare_choices(self):
        if not hasattr(self, "cmp_a"):
            return
        try:
            reports = db.list_reports()
        except Exception:
            reports = []
        for combo in (self.cmp_a, self.cmp_b):
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for r in reports:
                lvi = r.get("lvi_mean")
                lab = r["address"] + (f"   ·  LVI {lvi:.0f}" if isinstance(lvi, (int, float)) else "")
                combo.addItem(lab, r["id"])
            idx = combo.findData(cur)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        if self.cmp_b.count() > 1 and self.cmp_b.currentIndex() == self.cmp_a.currentIndex():
            self.cmp_b.setCurrentIndex(1)

    def on_compare(self):
        ida, idb = self.cmp_a.currentData(), self.cmp_b.currentData()
        if ida is None or idb is None:
            self.cmp_status.setText("Need two analyzed addresses — run reports in the Summary tab first.")
            return
        if ida == idb:
            self.cmp_status.setText("Pick two different addresses.")
            return
        self._cmp_repA, self._cmp_repB = db.get_report(ida), db.get_report(idb)
        if not self._cmp_repA or not self._cmp_repB:
            self.cmp_status.setText("Could not load one of the saved reports.")
            return
        self.compare_view.setHtml(compare.build_comparison_html(
            self._cmp_repA, self._cmp_repB, mapbox_key=self.cfg.get("mapbox_key", "")))
        self.cmp_ai_btn.setEnabled(True)
        self.cmp_status.setText("Comparison ready. Click 'Generate AI assessment' for a written analysis.")

    def on_compare_ai(self):
        if not (self._cmp_repA and self._cmp_repB):
            return
        self.cmp_ai_btn.setEnabled(False)
        self.cmp_ai_btn.setText("Generating…")
        self.cmp_status.setText("Asking Claude for a comparative assessment…")
        self._cmp_ai_worker = _CompareAIWorker(
            self.cfg.get("anthropic_key", ""), self._cmp_repA, self._cmp_repB)
        self._cmp_ai_worker.done.connect(self._on_compare_ai_done)
        self._cmp_ai_worker.start()

    def _on_compare_ai_done(self, ai_html):
        self.cmp_ai_btn.setEnabled(True)
        self.cmp_ai_btn.setText("🧠  Generate AI assessment")
        self.compare_view.setHtml(compare.build_comparison_html(
            self._cmp_repA, self._cmp_repB, ai_html=ai_html, mapbox_key=self.cfg.get("mapbox_key", "")))
        self.cmp_status.setText("AI assessment added below the map.")

    # ---------------- Saved reports tab ----------------
    def _build_saved_tab(self):
        v = QVBoxLayout(self.saved_tab)
        self.saved_list = QListWidget()
        self.saved_list.itemDoubleClicked.connect(self.on_open_saved_report)
        self.saved_list.setSelectionMode(QListWidget.ExtendedSelection)
        refresh_btn = QPushButton("Refresh list")
        refresh_btn.clicked.connect(self.refresh_saved_list)
        delete_btn = QPushButton("Delete selected")
        delete_btn.clicked.connect(self.on_delete_saved_report)
        compare_btn = QPushButton("Compare 2 selected")
        compare_btn.clicked.connect(self.on_compare_reports)
        btn_row = QHBoxLayout()
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(delete_btn)
        btn_row.addWidget(compare_btn)
        v.addWidget(QLabel("Double-click to reload a report. Ctrl-click two, then Compare, to get the "
                           "probability one site truly beats the other (back-test known practices this way)."))
        v.addWidget(self.saved_list)
        self.compare_out = QTextEdit(readOnly=True)
        self.compare_out.setMaximumHeight(120)
        v.addWidget(self.compare_out)
        v.addLayout(btn_row)
        self.refresh_saved_list()

    def on_compare_reports(self):
        import lvi as _lvi
        items = self.saved_list.selectedItems()
        if len(items) != 2:
            self.compare_out.setHtml(f"<span style='color:{PAL['text2']}'>Select exactly two saved reports "
                                     "(Ctrl-click), then press Compare.</span>")
            return
        recs = []
        for it in items:
            r = next((x for x in db.list_reports() if x["id"] == it.data(Qt.UserRole)), None)
            if r and r.get("lvi_mean") is not None:
                sd = ((r.get("lvi_p95", 0) or 0) - (r.get("lvi_p05", 0) or 0)) / 3.29 or 1.0
                recs.append((r["address"], r["lvi_mean"], sd))
        if len(recs) != 2:
            self.compare_out.setHtml(f"<span style='color:{PAL['text2']}'>Those reports lack LVI data.</span>")
            return
        (na, ma, sa), (nb, mb, sb) = recs
        p = _lvi.dominance_probability(ma, sa, mb, sb)
        winner, prob = (na, p) if p >= 50 else (nb, 100 - p)
        verdict = ("decisive" if prob >= 90 else "likely" if prob >= 70 else "within noise (not distinguishable)")
        self.compare_out.setHtml(
            f"<div style='font-size:13px;'><b>{na}</b> (LVI {ma}±{sa:.1f}) vs <b>{nb}</b> (LVI {mb}±{sb:.1f})<br>"
            f"P(<b>{winner}</b> is truly better) = <b style='color:{PAL['blue']}'>{prob:.1f}%</b> — {verdict}.<br>"
            f"<span style='color:{PAL['text3']}; font-size:11px;'>Closed-form from the Monte-Carlo posteriors; "
            "overlapping credible intervals → the gap is not meaningful.</span></div>")

    def refresh_saved_list(self):
        self.saved_list.clear()
        for r in db.list_reports():
            mean = r["lvi_mean"]
            label = f"#{r['id']} — {r['address']} — LVI mean {mean if mean is not None else 'n/a'}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, r["id"])
            self.saved_list.addItem(item)

    def on_open_saved_report(self, item: QListWidgetItem):
        rid = item.data(Qt.UserRole)
        rep = db.get_report(rid)
        if rep:
            rep["_db_id"] = rid
            self.render_report(rep)
            self.tabs.setCurrentIndex(0)

    def on_delete_saved_report(self):
        item = self.saved_list.currentItem()
        if not item:
            return
        db.delete_report(item.data(Qt.UserRole))
        self.refresh_saved_list()

    # ---------------- Settings tab ----------------
    def _build_settings_tab(self):
        v = QVBoxLayout(self.settings_tab)
        form_group = QGroupBox("API keys")
        form = QFormLayout(form_group)
        self.google_key_input = QLineEdit(self.cfg.get("google_places_api_key", ""))
        self.google_key_input.setEchoMode(QLineEdit.Password)
        self.census_key_input = QLineEdit(self.cfg.get("census_api_key", ""))
        self.mapbox_key_input = QLineEdit(self.cfg.get("mapbox_key", ""))
        self.anthropic_key_input = QLineEdit(self.cfg.get("anthropic_key", ""))
        self.anthropic_key_input.setEchoMode(QLineEdit.Password)
        self.apify_key_input = QLineEdit(self.cfg.get("apify_key", ""))
        self.apify_key_input.setEchoMode(QLineEdit.Password)
        form.addRow("Google Places API key (optional — free OpenStreetMap is used otherwise):", self.google_key_input)
        form.addRow("Census API key (required for all demographics):", self.census_key_input)
        form.addRow("Mapbox token (Site Scout drive-time isochrones; free tier, OSRM fallback):", self.mapbox_key_input)
        form.addRow("Anthropic key (Site Scout Claude Vision — optional):", self.anthropic_key_input)
        form.addRow("Apify token (Site Scout live LoopNet/Crexi — optional, paid):", self.apify_key_input)
        v.addWidget(form_group)

        # --- Known-competitor watchlist (catches specialists the NPI taxonomy misses) ---
        watch_group = QGroupBox("Known-competitor watchlist  —  one per line:  Name | https://url | ZIP")
        watch_layout = QVBoxLayout(watch_group)
        watch_hint = QLabel(
            "Specialists like Dr. Shirazi and Dr. Borquez register in the NPI database as general "
            "dentists, so the taxonomy search can't auto-detect them — their TMJ/orofacial-pain/sleep "
            "focus is only on their websites. List them here and the app website-verifies each one on "
            "every report and always shows them as competitors. Add or remove any competitor."
        )
        watch_hint.setWordWrap(True)
        watch_hint.setStyleSheet("color:#8295b0; font-size:11px;")
        self.watch_input = QPlainTextEdit()
        self.watch_input.setPlaceholderText("Dr. Jane Doe | https://example-tmj.com | 91361")
        self.watch_input.setPlainText(self._watchlist_to_text(self.cfg.get("known_competitors", [])))
        self.watch_input.setMaximumHeight(120)
        watch_layout.addWidget(watch_hint)
        watch_layout.addWidget(self.watch_input)
        v.addWidget(watch_group)

        save_btn = QPushButton("Save settings")
        save_btn.clicked.connect(self.on_save_settings)
        v.addWidget(save_btn)
        note = QTextEdit(readOnly=True)
        note.setPlainText(
            "Google Places API key is OPTIONAL. By default, Competitors and Referral Map use "
            "OpenStreetMap's free Overpass API — no key, no billing, no account needed. It finds "
            "fewer results and has no ratings/review data, but the competitor scoring (board-cert "
            "keyword scan of each practice's own website) works the same either way. If you want "
            "richer Google-quality results and don't mind the cost: create a project at "
            "console.cloud.google.com, enable 'Places API', enable billing, and paste the key here.\n\n"
            "Census API key is REQUIRED — confirmed live on 2026-06-20, the Census Bureau now "
            "rejects every ACS request without a key, even single low-volume lookups. It's free "
            "and instant: sign up at api.census.gov/data/key_signup.html and paste the key here. "
            "Without it, the Demographics and Statistics tabs will fail.\n\n"
            f"Config file in use by this app: {config.CONFIG_PATH}"
        )
        v.addWidget(note)

    @staticmethod
    def _watchlist_to_text(entries) -> str:
        lines = []
        for e in entries or []:
            lines.append(f"{e.get('name','')} | {e.get('url','')} | {e.get('zip','')}")
        return "\n".join(lines)

    @staticmethod
    def _text_to_watchlist(text) -> list:
        out = []
        for line in (text or "").splitlines():
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split("|")]
            name = parts[0] if len(parts) > 0 else ""
            url = parts[1] if len(parts) > 1 else ""
            zip5 = parts[2] if len(parts) > 2 else ""
            if name and url:
                out.append({"name": name, "url": url, "zip": zip5})
        return out

    def on_save_settings(self):
        self.cfg["google_places_api_key"] = self.google_key_input.text().strip()
        self.cfg["census_api_key"] = self.census_key_input.text().strip()
        self.cfg["mapbox_key"] = self.mapbox_key_input.text().strip()
        self.cfg["anthropic_key"] = self.anthropic_key_input.text().strip()
        self.cfg["apify_key"] = self.apify_key_input.text().strip()
        self.cfg["known_competitors"] = self._text_to_watchlist(self.watch_input.toPlainText())
        config.save_config(self.cfg)
        QMessageBox.information(self, "Saved", "Settings saved.")

    # ---------------- Report execution ----------------
    def on_run_report(self):
        address = self.address_input.text().strip()
        if not address:
            QMessageBox.warning(self, "Missing address", "Enter an address first.")
            return
        self.run_btn.setEnabled(False)
        self.progress_bar.show()
        self.progress_label.setText("Starting...")
        self.worker = ReportWorker(
            address,
            self.cfg.get("google_places_api_key", ""),
            self.cfg.get("census_api_key", ""),
            known_competitors=self.cfg.get("known_competitors", []),
        )
        self.worker.progress.connect(lambda m: self.progress_label.setText(m))
        self.worker.finished_ok.connect(self.on_report_done)
        self.worker.finished_err.connect(self.on_report_error)
        self.worker.start()

    def on_report_error(self, msg):
        self.run_btn.setEnabled(True)
        self.progress_bar.hide()
        self.progress_label.setText("Failed.")
        QMessageBox.critical(self, "Report failed", msg)

    def on_report_done(self, rep: dict):
        self.run_btn.setEnabled(True)
        self.progress_bar.hide()
        self.progress_label.setText("Done.")
        rid = db.save_report(rep.get("address_input", ""), rep)
        rep["_db_id"] = rid
        self.render_report(rep)
        self.refresh_saved_list()

    def on_export_pdf(self):
        if not self.current_report:
            QMessageBox.information(self, "Nothing to export", "Run a report first.")
            return
        addr = (self.current_report.get("address_input") or "report").strip()
        safe = "".join(c if c.isalnum() or c in " -_" else "" for c in addr).strip().replace(" ", "_")[:60] or "report"
        default_name = f"ClinicSiteIntel_{safe}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Export report as PDF", default_name, "PDF files (*.pdf)")
        if not path:
            return
        layout = QPageLayout(QPageSize(QPageSize.Letter), QPageLayout.Portrait,
                              QMarginsF(14, 12, 14, 14), QPageLayout.Millimeter)
        self.export_pdf_btn.setEnabled(False)
        self.export_pdf_btn.setText("Exporting…")

        page = self.summary_tab.page()

        def _done(finished_path, ok):
            try:
                page.pdfPrintingFinished.disconnect(_done)
            except (TypeError, RuntimeError):
                pass
            self.export_pdf_btn.setEnabled(True)
            self.export_pdf_btn.setText("⬇ Export PDF")
            if ok:
                self.progress_label.setText(f"Saved PDF: {finished_path}")
            else:
                QMessageBox.critical(self, "Export failed", "Could not write the PDF. Check the path and try again.")

        page.pdfPrintingFinished.connect(_done)
        page.printToPdf(path, layout)

    # ---------------- Rendering ----------------
    def render_report(self, rep: dict):
        self.current_report = rep
        self.export_pdf_btn.setEnabled(True)
        self._render_summary(rep)
        self._render_demographics(rep)
        self._render_competitors(rep)
        self._render_referrals(rep)
        self._render_realestate(rep)
        self._render_stats(rep)
        self._refresh_compare_choices()   # new report now selectable in Compare

    @staticmethod
    def _card(title, body_html):
        return (
            f'<div style="background:{PAL["surface"]}; border:1px solid {PAL["border"]}; border-radius:14px; '
            f'padding:16px 20px; margin-bottom:14px;">'
            f'<div style="color:{PAL["text2"]}; font-weight:700; font-size:12px; letter-spacing:0.4px; '
            f'margin-bottom:10px;">{title}</div>'
            f'{body_html}</div>'
        )

    @staticmethod
    def _fmt_money(v):
        return f"${v:,.0f}" if v is not None else f"<span style='color:{PAL['text3']}'>n/a</span>"

    @staticmethod
    def _fmt_pct(v):
        return f"{v:.1f}%" if v is not None else f"<span style='color:{PAL['text3']}'>n/a</span>"

    def _interpretation_section(self, rep):
        """Plain-language executive interpretation: synthesizes LVI, demographics,
        competition, referral substrate and the spatial models into one verdict
        with a recommended action."""
        lvi = (rep.get("lvi_summary") or {}).get("mean")
        demo = rep.get("demographics_zip") or rep.get("demographics_tract") or {}
        comps = rep.get("competitors", [])
        refs = rep.get("referrals", [])
        sp = rep.get("spatial") or {}

        specialists = [c for c in comps if str(c.get("tier", "")).startswith("Specialist")]
        nearest_spec = min([c.get("distance_mi") for c in specialists if c.get("distance_mi") is not None], default=None)
        n_md = sum(1 for r in refs if str(r.get("category", "")).startswith("Physician"))
        income = demo.get("median_household_income")
        age = demo.get("median_age")
        huff = sp.get("huff_share_pct")
        sfca = sp.get("sfca_pct")

        bullets = []
        # Market / demographics
        if income is not None and age is not None:
            fit = "strong" if (income >= 100000 and age >= 40) else ("moderate" if income >= 75000 else "weak")
            bullets.append(
                f"<b>Market.</b> Median household income {self._fmt_money(income)} and median age "
                f"{age} indicate a <b>{fit}</b> demographic fit for an affluent, 40+ OSA/TMD cash-pay cohort.")
        # Competition
        if specialists:
            near = f"the nearest just {nearest_spec} mi away" if nearest_spec is not None else "nearby"
            bullets.append(
                f"<b>Competition.</b> <span style='color:{PAL['red']}'>{len(specialists)} credentialed "
                f"orofacial-pain/TMJ/sleep specialist(s)</span> already operate in this trade area, with {near}. "
                "This is a contested market — differentiation and referral relationships matter more than being first.")
        else:
            bullets.append(
                "<b>Competition.</b> No credentialed orofacial-pain specialist detected nearby — a potential "
                "open-market signal, but confirm before relying on it.")
        # Referrals
        if n_md:
            bullets.append(
                f"<b>Referral substrate.</b> <span style='color:{PAL['blue']}'>{n_md} physicians</span> across all "
                "specialties sit within the catchment (sleep medicine, ENT, neurology, primary care) — a deep pool "
                "of potential referrers feeding OSA/TMD diagnoses.")
        # Spatial
        launch = sp.get("huff_launch_pct")
        if huff is not None:
            sat = ("a saturated" if (sfca or 0) >= 75 else "an elevated" if (sfca or 0) >= 45 else "an underserved")
            lr = f" (year-1 ~<b>{launch}%</b> before the practice matures)" if launch is not None else ""
            bullets.append(
                f"<b>Spatial position.</b> Gravity models predict ~<b>{huff}%</b> steady-state demand capture{lr} into "
                f"{sat} existing-supply market.")
        # Unit economics
        econ_d = rep.get("econ") or {}
        be, proj = econ_d.get("break_even_cases"), econ_d.get("projected_cases")
        if be and proj is not None:
            ecol = PAL["green"] if proj >= be else PAL["red"]
            bullets.append(
                f"<b>Unit economics.</b> Projected ~<span style='color:{ecol}'>{proj:,.0f} cases/yr</span> vs. "
                f"~{be:,.0f} needed to break even — "
                + ("clears break-even with cushion." if proj >= 1.5*be else
                   "roughly at break-even; thin margin." if proj >= be else "below break-even at default assumptions."))

        # ---- Overall verdict (now includes unit economics & launch ramp) ----
        score = 0
        if lvi is not None:
            score += 2 if lvi >= 60 else (1 if lvi >= 48 else 0)
        if income and income >= 100000: score += 1
        if specialists and (nearest_spec or 99) < 2: score -= 1
        if not specialists: score += 1
        if n_md >= 30: score += 1
        if huff is not None and huff >= 20: score += 1
        if (sfca or 0) >= 75: score -= 1
        elif (sfca or 0) < 45: score += 1
        if be and proj is not None:
            score += 2 if proj >= 1.5 * be else (1 if proj >= be else -1)

        if score >= 5:
            band, bcolor, rec = "PURSUE", PAL["green"], "Advance to broker outreach and real-estate diligence on this corridor."
        elif score >= 2:
            band, bcolor, rec = "PURSUE WITH CONDITIONS", PAL["amber"], ("Viable but contested — proceed only with a clear "
                "differentiation plan (specialty depth, weekend access, referral pipeline) and confirm a suitable suite.")
        else:
            band, bcolor, rec = "CAUTION", PAL["red"], ("Spatial/competitive headwinds here — prefer a less saturated or "
                "better-positioned candidate before committing.")

        bullet_html = "".join(f"<li style='margin-bottom:7px; line-height:1.5;'>{b}</li>" for b in bullets)
        body = (
            f"<div style='display:inline-block; background:{bcolor}; color:#fff; font-weight:800; "
            f"font-size:13px; padding:5px 14px; border-radius:20px; margin-bottom:12px;'>VERDICT: {band}</div>"
            f"<ul style='margin:0 0 12px; padding-left:18px; color:{PAL['text']}; font-size:13px;'>{bullet_html}</ul>"
            f"<div style='padding:12px 14px; background:{PAL['surface2']}; border-radius:10px; "
            f"border-left:3px solid {bcolor};'>"
            f"<span style='color:{PAL['text2']}; font-weight:700; font-size:11px;'>RECOMMENDED ACTION</span><br>"
            f"<span style='color:{PAL['text']}; font-size:13px;'>{rec}</span></div>"
        )
        return self._card("EXECUTIVE INTERPRETATION", body)

    def _consultant_section(self, rep):
        """Consultant's Read: a sourced contribution table + plain-English
        narrative of how the verdict is built, generated from the live report."""
        try:
            import narrative
            body = narrative.build_consultant_read(rep, pal=PAL)
        except Exception as e:
            body = f"<span style='color:{PAL['text3']}'>Consultant narrative unavailable: {e}</span>"
        return self._card("CONSULTANT'S READ — HOW THIS VERDICT IS BUILT", body)

    def _render_summary(self, rep):
        geo = rep.get("geo", {})
        import narrative
        try:
            html = narrative.build_summary_html(rep)
        except Exception as e:
            import traceback as _tb
            html = (f"<body style='font-family:Segoe UI;padding:24px;color:#b00;'>"
                    f"<h3>Summary render failed</h3><pre>{e}\n\n{_tb.format_exc()}</pre></body>")
        self.summary_tab.setHtml(html)

    def _spatial_section(self, sp):
        """Lower-section spatial-interaction analysis: the four models + a
        combined comparison verdict, per the requested methodology."""
        if not sp:
            return ""
        if not sp.get("ok"):
            return self._card("SPATIAL INTERACTION ANALYSIS",
                              f"<span style='color:{PAL['text3']}'>{sp.get('note','Unavailable for this address.')}</span>")
        # model cards
        body = "<table style='width:100%; border-collapse:collapse;'>"
        for model, value, reading in sp.get("rows", []):
            body += (
                f"<tr>"
                f"<td style='padding:8px 8px; border-bottom:1px solid {PAL['border']}; width:33%; "
                f"color:{PAL['text']}; font-weight:600; vertical-align:top;'>{model}</td>"
                f"<td style='padding:8px 8px; border-bottom:1px solid {PAL['border']}; width:22%; "
                f"color:{PAL['blue']}; font-weight:700; vertical-align:top;'>{value}</td>"
                f"<td style='padding:8px 8px; border-bottom:1px solid {PAL['border']}; "
                f"color:{PAL['text2']}; font-size:12px; vertical-align:top;'>{reading}</td>"
                f"</tr>"
            )
        body += "</table>"

        verdict = sp.get("verdict", "")
        vlow = verdict.lower()
        vcolor = PAL["green"] if "favorable" in vlow and "unfav" not in vlow else (
            PAL["amber"] if "marginal" in vlow else PAL["red"])
        body += (
            f"<div style='margin-top:14px; padding:13px 15px; background:{PAL['surface2']}; "
            f"border-radius:10px; border-left:3px solid {vcolor};'>"
            f"<div style='color:{vcolor}; font-weight:800; font-size:13px; margin-bottom:4px;'>"
            f"COMBINED SPATIAL VERDICT</div>"
            f"<div style='color:{PAL['text']}; font-size:12.5px; line-height:1.5;'>{verdict}</div></div>"
            f"<div style='color:{PAL['text3']}; font-size:11px; margin-top:8px;'>"
            "Demand = epidemiology-weighted expected cases (CDC PLACES OSA-risk × ACS age/income) over "
            "real-geocoded competitors, retirement-adjusted; capture shown as a β-sensitivity band with a "
            "year-1 launch ramp. Structural estimates, not real patient-flow data.</div>"
        )
        return self._card("SPATIAL INTERACTION & LOCATION-ALLOCATION ANALYSIS", body)

    def _econ_section(self, e):
        if not e:
            return ""
        margin = e.get("margin_cases", 0)
        color = PAL["green"] if margin >= 0.5 * (e.get("break_even_cases") or 1) else (
            PAL["amber"] if margin >= 0 else PAL["red"])
        body = (
            f"<table style='width:100%; border-collapse:collapse;'>"
            f"<tr><td style='padding:6px 0; color:{PAL['text2']}; width:55%;'>Annual fixed cost (rent + build-out amort. + labor)</td>"
            f"<td style='padding:6px 0; color:{PAL['text']}; font-weight:600;'>${e.get('fixed_annual',0):,.0f}</td></tr>"
            f"<tr><td style='padding:6px 0; color:{PAL['text2']};'>Contribution per case</td>"
            f"<td style='padding:6px 0; color:{PAL['text']}; font-weight:600;'>${e.get('contribution_per_case',0):,.0f}</td></tr>"
            f"<tr><td style='padding:6px 0; color:{PAL['text2']};'>Break-even volume</td>"
            f"<td style='padding:6px 0; color:{PAL['text']}; font-weight:700;'>≈{e.get('break_even_cases',0):,.0f} cases/yr</td></tr>"
            f"<tr><td style='padding:6px 0; color:{PAL['text2']};'>Projected volume (from Huff capture × conversion)</td>"
            f"<td style='padding:6px 0; color:{color}; font-weight:700;'>≈{e.get('projected_cases',0):,.0f} cases/yr</td></tr>"
            f"</table>"
            f"<div style='margin-top:10px; padding:11px 14px; background:{PAL['surface2']}; border-radius:10px; "
            f"border-left:3px solid {color};'><span style='color:{PAL['text']}; font-size:12.5px;'>{e.get('verdict','')}</span></div>"
            f"<div style='color:{PAL['text3']}; font-size:11px; margin-top:7px;'>Default cost/revenue assumptions "
            "(editable in code): $33/SF·yr rent, 1,800 SF, $250k build-out / 7 yr, $240k labor, $2,600 revenue & "
            "$650 variable per case, 20% annual conversion of captured prevalent demand.</div>"
        )
        return self._card("UNIT-ECONOMICS OVERLAY — BREAK-EVEN vs. PREDICTED CAPTURE", body)

    def _sensitivity_section(self, rep):
        rows = rep.get("lvi_sensitivity")
        moe = rep.get("acs_moe") or {}
        if not rows:
            return ""
        bars = ""
        for label, pct in rows:
            bars += (
                f"<div style='margin-bottom:6px;'>"
                f"<div style='display:flex; justify-content:space-between; font-size:12px; color:{PAL['text']};'>"
                f"<span>{label}</span><span style='color:{PAL['text2']};'>{pct}%</span></div>"
                f"<div style='background:{PAL['border']}; border-radius:4px; height:7px; margin-top:2px;'>"
                f"<div style='background:{PAL['blue']}; width:{min(pct,100)}%; height:7px; border-radius:4px;'></div></div></div>"
            )
        moe_note = ""
        if moe.get("income") and moe.get("income_moe"):
            moe_note = (f"<div style='color:{PAL['text3']}; font-size:11px; margin-top:8px;'>"
                        f"ACS sampling error propagated into the score: tract income "
                        f"${moe['income']:,.0f} ± ${moe['income_moe']:,.0f} (90% MOE).</div>")
        body = (
            f"<div style='color:{PAL['text2']}; font-size:12px; margin-bottom:10px;'>Share of the LVI's "
            "uncertainty driven by each input — i.e., which data is most worth improving next.</div>"
            f"{bars}{moe_note}"
        )
        return self._card("SENSITIVITY — WHAT DRIVES THE UNCERTAINTY (first-order Sobol)", body)

    def _render_demographics(self, rep):
        def block(label, d):
            if not d:
                return self._card(label, "<span style='color:#aeaeb2'>Unavailable — see Summary tab data-gap notes.</span>")
            rows = [
                ("Geography", d.get("geo_label")),
                ("Population", f"{d.get('population'):,.0f}" if d.get("population") is not None else "n/a"),
                ("Median household income", self._fmt_money(d.get("median_household_income"))),
                ("Median age", d.get("median_age") if d.get("median_age") is not None else "n/a"),
                ("Median home value", self._fmt_money(d.get("median_home_value"))),
                ("Median gross rent", self._fmt_money(d.get("median_gross_rent"))),
                ("Poverty rate", self._fmt_pct(d.get("poverty_rate_pct"))),
                ("Unemployment rate", self._fmt_pct(d.get("unemployment_rate_pct"))),
            ]
            body = "<table style='width:100%; border-collapse:collapse;'>"
            for k, v in rows:
                body += (
                    f"<tr><td style='padding:3px 0; color:#6e6e73; width:55%;'>{k}</td>"
                    f"<td style='padding:3px 0; color:#1c1c1e; font-weight:600;'>{v}</td></tr>"
                )
            body += "</table>"
            return self._card(label, body)

        html = block("TRACT-LEVEL (address-based)", rep.get("demographics_tract"))
        html += block("ZIP-LEVEL (ZCTA-based)", rep.get("demographics_zip"))
        html += (
            "<div style='color:#aeaeb2; font-size:11.5px;'>Source: US Census Bureau ACS 5-Year "
            "Estimates — live API pull on every report run, not cached/static data.</div>"
        )
        self.demo_tab.setHtml(html)

    def _render_competitors(self, rep):
        comps = rep.get("competitors", [])
        table = self.competitors_table
        cols = ["Tier", "Name", "Address", "Dist (mi)", "Score", "Credentials", "Verification", "Website", "Phone"]
        table.setSortingEnabled(False)   # don't reorder mid-populate
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.setRowCount(len(comps))
        for i, c in enumerate(comps):
            tier = c.get("tier", "")
            vals = [
                tier,
                c.get("name", ""),
                c.get("address", ""),
                f"{c.get('distance_mi', 0):.1f}" if c.get("distance_mi") is not None else "",
                str(c.get("competition_score", 0)),
                " · ".join(c.get("credentials", [])) or "—",
                c.get("verification_note", ""),
                c.get("website") or "",
                c.get("phone") or "",
            ]
            is_specialist = str(c.get("tier", "")).startswith("Specialist")
            for j, v in enumerate(vals):
                item = _SortItem(v)
                if is_specialist:
                    item.setForeground(QColor("#ff3b30"))  # iOS red = real specialist threat
                elif j == 0:
                    item.setForeground(QColor("#8e8e93"))
                table.setItem(i, j, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.setSortingEnabled(True)
        table.sortItems(4, Qt.DescendingOrder)   # default: highest competition score first

        n_spec = sum(1 for c in comps if c.get("tier") == "Specialist")
        if not comps:
            self.competitors_status.setText(
                "No competitors found: no NPI-registered orofacial-pain specialist in this ZIP region, "
                "and no nearby general dentist advertises TMJ / dental-sleep services. An open-market "
                "signal — but confirm independently before relying on it."
            )
            self.competitors_status.show()
        else:
            ps = rep.get("places_status") or {}
            base = (f"COMPETITORS = dentists who DO orofacial pain / TMJ / dental sleep. "
                    f"{n_spec} credentialed orofacial-pain specialist(s) (red) — COMPLETE, from the NPI "
                    "Registry by taxonomy. Plus general dentists whose website advertises TMJ/sleep. ")
            if ps.get("google_error"):
                tail = ("⚠ Google Places (New) FAILED: " + ps["google_error"]
                        + " — enable 'Places API (New)' + active billing on your project (or the key is "
                        "invalid). Until then, general-dentist coverage is OpenStreetMap-only (partial).")
            elif ps.get("used_google"):
                tail = ("✓ Google Places (New) live — full general-dentist coverage + the building "
                        "directory (in-building tenants) for this address.")
            else:
                tail = ("Note: general-dentist coverage is PARTIAL in free mode (OpenStreetMap only). "
                        "Add a Google Places key (Settings) for complete coverage + the building directory.")
            self.competitors_status.setText(base + tail)
            self.competitors_status.show()

    def _render_referrals(self, rep):
        refs = rep.get("referrals", [])
        table = self.referrals_table
        cols = ["Type", "Name", "Specialty", "Address", "Dist (mi)", "Fit Score", "Phone", "Credentials"]
        table.setSortingEnabled(False)
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.setRowCount(len(refs))
        for i, r in enumerate(refs):
            category = r.get("category", "")
            is_md = category.startswith("Physician")
            vals = [
                "MD/DO" if is_md else "DDS",
                r.get("name", ""),
                r.get("specialty", ""),
                r.get("address", ""),
                f"{r.get('distance_mi', 0):.1f}" if r.get("distance_mi") is not None else "",
                str(r.get("referral_score", "")),
                r.get("phone", "") or "",
                " · ".join(r.get("credentials", [])) or "—",
            ]
            for j, v in enumerate(vals):
                item = _SortItem(v)
                if j == 0:
                    item.setForeground(QColor("#007aff") if is_md else QColor("#ff9500"))
                table.setItem(i, j, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.setSortingEnabled(True)
        table.sortItems(5, Qt.DescendingOrder)   # default: highest referral fit first

        n_md = sum(1 for r in refs if str(r.get("category", "")).startswith("Physician"))
        n_dds = len(refs) - n_md
        if not refs:
            self.referrals_status.setText(
                "No referral candidates found in this ZIP region (NPI Registry returned no nearby "
                "physicians or non-competing dentists). Confirm the ZIP resolved correctly."
            )
            self.referrals_status.show()
        else:
            self.referrals_status.setText(
                f"REFERRAL SOURCES = {n_md} physicians (MD/DO, blue — all specialties via the NPI "
                f"Registry: sleep medicine, ENT, neurology, primary care…) + {n_dds} general dentists "
                "(gold) who do NOT advertise TMJ/sleep and could refer those cases to you. Distances "
                "are ZIP-centroid approximations."
            )
            self.referrals_status.show()

    def _render_realestate(self, rep):
        self.realestate_links_list.clear()
        for name, url in rep.get("realestate_links", {}).items():
            item = QListWidgetItem(f"{name}  —  {url}")
            item.setData(Qt.UserRole, url)
            self.realestate_links_list.addItem(item)

    def _render_stats(self, rep):
        s = rep.get("zip_basket_stats")
        if not s:
            self.stats_tab.setHtml(
                self._card("ZIP-BASKET STATISTICS", "<span style='color:#aeaeb2'>Unavailable for this address — see Summary tab data-gap notes.</span>")
            )
            return

        def corr_badge(r):
            if r is None:
                return "<span style='color:#aeaeb2'>n/a</span>"
            color = "#34c759" if abs(r) >= 0.6 else ("#007aff" if abs(r) >= 0.3 else "#ff3b30")
            return f"<b style='color:{color}'>{r:.3f}</b>"

        html = ""
        # REAL external test first (non-circular)
        ext = rep.get("external_regression")
        if ext and ext.get("r") is not None:
            rows = "".join(
                f"<tr><td style='padding:3px 8px; color:{PAL['text']};'>{p['zip']}</td>"
                f"<td style='padding:3px 8px; color:{PAL['text']};'>${p['income']:,.0f}</td>"
                f"<td style='padding:3px 8px; color:{PAL['text']};'>{p['dentists']}</td>"
                f"<td style='padding:3px 8px; color:{PAL['blue']}; font-weight:600;'>{p['per10k']}</td></tr>"
                for p in ext.get("points", []))
            html += self._card(
                "EXTERNAL VALIDATION TEST — specialist supply/100k vs. income (independent)",
                f"<div style='color:{PAL['text']}; font-size:13px; margin-bottom:10px;'>{ext['reading']}</div>"
                f"<table style='width:100%; border-collapse:collapse; font-size:12.5px;'>"
                f"<tr style='color:{PAL['text2']};'><td style='padding:3px 8px;'>ZIP</td>"
                f"<td style='padding:3px 8px;'>Median income</td><td style='padding:3px 8px;'># Specialists (NPI)</td>"
                f"<td style='padding:3px 8px;'>Supply / 100k</td></tr>{rows}</table>")

        html += self._card(
            f"INTERNAL CONSISTENCY (mechanical, not an independent test) — {s.get('n')} ZCTAs",
            f"""
            <table style="width:100%; border-collapse:collapse;">
            <tr><td style="padding:3px 0; color:#6e6e73;">Median income vs. demographic-fit proxy</td>
                <td style="padding:3px 0;">{corr_badge(s.get('income_corr'))}</td></tr>
            <tr><td style="padding:3px 0; color:#6e6e73;">Median age vs. demographic-fit proxy</td>
                <td style="padding:3px 0;">{corr_badge(s.get('age_corr'))}</td></tr>
            <tr><td style="padding:3px 0; color:#6e6e73;">Population vs. demographic-fit proxy</td>
                <td style="padding:3px 0;">{corr_badge(s.get('pop_corr'))}</td></tr>
            </table>
            """,
        )

        rows_html = (
            "<table style='width:100%; border-collapse:collapse; font-size:12.5px;'>"
            "<tr style='color:#007aff;'><td style='padding:4px 6px;'>ZIP</td><td style='padding:4px 6px;'>Population</td>"
            "<td style='padding:4px 6px;'>Median income</td><td style='padding:4px 6px;'>Median age</td>"
            "<td style='padding:4px 6px;'>Proxy score</td></tr>"
        )
        for row in s.get("rows", []):
            pop = f"{row.get('population'):,.0f}" if row.get("population") is not None else "n/a"
            inc = self._fmt_money(row.get("median_income"))
            age = row.get("median_age") if row.get("median_age") is not None else "n/a"
            proxy = row.get("proxy_score")
            rows_html += (
                f"<tr style='border-top:1px solid #e5e5ea;'>"
                f"<td style='padding:4px 6px; color:#1c1c1e;'>{row.get('zip_code')}</td>"
                f"<td style='padding:4px 6px; color:#1c1c1e;'>{pop}</td>"
                f"<td style='padding:4px 6px; color:#1c1c1e;'>{inc}</td>"
                f"<td style='padding:4px 6px; color:#1c1c1e;'>{age}</td>"
                f"<td style='padding:4px 6px; color:#007aff; font-weight:600;'>{proxy:.1f}</td></tr>"
            )
        rows_html += "</table>"
        html += self._card("PER-ZIP DETAIL", rows_html)
        html += self._card("CAVEAT", f"<span style='color:#6e6e73; font-size:12px;'>{s.get('caveat', '')}</span>")
        self.stats_tab.setHtml(html)


def main():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setStyleSheet(LIGHT_STYLE)
    app.setFont(QFont("Segoe UI", 10))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
