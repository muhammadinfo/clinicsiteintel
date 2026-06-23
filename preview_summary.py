"""Standalone preview of the premium WebEngine Summary — renders the HTML to a
PNG so the design can be reviewed without rebuilding the whole app."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "app"))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QSize
from PySide6.QtWebEngineWidgets import QWebEngineView
import narrative

REP = {
    "geo": {"matched_address": "2220 Lynn Rd, Thousand Oaks, CA, 91360",
            "lat": 34.2084, "lon": -118.8851, "zip_code": "91360"},
    "lvi_inputs": {"ds": 77.0, "rp": 90.0, "if_": 78.0, "cp": 35.0, "of_": 50.0, "rc": 50.0},
    "lvi_summary": {"mean": 67.8, "point_estimate": 68.0, "sd": 6.8, "p05": 56.5, "p95": 78.8},
    "demographics_zip": {"median_household_income": 111095, "median_age": 43.1},
    "competitors": [{"tier": "Specialist", "distance_mi": 1.06, "competition_score": 90},
                    {"tier": "Specialist (watchlist)", "distance_mi": 1.7, "competition_score": 80}],
    "referrals": ([{"category": "Physician (MD/DO)", "distance_mi": 0.05},
                   {"category": "Physician (MD/DO)", "distance_mi": 0.1}]
                  + [{"category": "Physician (MD/DO)", "distance_mi": 3} for _ in range(62)]),
    "spatial": {"ok": True, "huff_share_pct": 17.6, "huff_lo": 17.4, "huff_hi": 17.6,
                "huff_launch_pct": 9.4, "huff_captured_pop": 1820, "mci_share_pct": 19.2,
                "sfca_pct": 62, "pmedian_efficiency_pct": 88, "breakpoint_mi": 1.4, "nn_index": 0.83,
                "verdict": "Favorable: an underserved-supply pocket with strong gravity capture and a defensible "
                           "trade-area boundary; competitors are mildly clustered to the east."},
    "econ": {"projected_cases": 847, "break_even_cases": 172, "fixed_annual": 412000,
             "contribution_per_case": 2400, "margin_cases": 675},
    "places": {"sleep": 33.0, "obesity": 28.0, "bphigh": 30.0, "osa_index": 0.94},
    "lvi_sensitivity": [("Demographic fit (Dₛ)", 31.2), ("Rent burden (R_c)", 26.0),
                        ("Referral density (Rₚ)", 18.4), ("Infrastructure (I_f)", 14.1),
                        ("Competition (Cₚ)", 6.3), ("Operations (O_f)", 4.0)],
    "acs_moe": {"income": 111095, "income_moe": 8420, "age_moe": 1.1},
}

OUT = os.path.join(HERE, "_summary_preview.png")

app = QApplication(sys.argv)
view = QWebEngineView()
view.resize(QSize(1000, 2820))
view.setHtml(narrative.build_summary_html(REP))
view.show()


OUT2 = os.path.join(HERE, "_summary_preview2.png")


def grab_top():
    view.grab().save(OUT)
    print("saved", OUT)
    view.page().runJavaScript("window.scrollTo(0, document.body.scrollHeight - 1350);")
    QTimer.singleShot(700, grab_low)


def grab_low():
    view.grab().save(OUT2)
    print("saved", OUT2)
    app.quit()


view.loadFinished.connect(lambda ok: QTimer.singleShot(900, grab_top))
QTimer.singleShot(8000, lambda: app.quit())  # fallback
sys.exit(app.exec())
