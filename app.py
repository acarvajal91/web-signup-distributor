"""
Web Sign-up Distributor — Streamlit App
"""

from datetime import date

import pandas as pd
import streamlit as st

import sheets as sh
from distributor import distribute, fairness_report

st.set_page_config(
    page_title="Web Sign-up Distributor",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  .block-container { padding-top: 1.5rem; }
  .stDataFrame { font-size: 13px; }
  div[data-testid="metric-container"] { background: #f8f8f8; border-radius: 8px; padding: 10px; }
</style>
""", unsafe_allow_html=True)

SPREADSHEET_ID = st.secrets.get("spreadsheet_id", "")

with st.sidebar:
    st.title("⚡ Web Sign-up Distributor")
    st.caption("Bodas.net · ES market")
    st.divider()
    page = st.radio("Navegación", [
        "📤 Subir sign-ups del día",
        "📊 Historial del mes",
        "⚙️ Configuración",
    ])

@st.cache_data(ttl=30)
def get_reps() -> list[str]:
    return sh.load_reps(SPREADSHEET_ID) or list(st.secrets.get("reps", [
        "Rep 1", "Rep 2", "Rep 3", "Rep 4", "Rep 5", "Rep 6", "Rep 7"
    ]))

@st.cache_data(ttl=30)
def get_excluded_cats() -> list[str]:
    return sh.load_excluded_cats(SPREADSHEET_ID)


def parse_upload(uploaded_file) -> pd.DataFrame:
    if uploaded_file.name.endswith(".csv"):
        df = pd.read_csv(uploaded_file)
    else:
        df = pd.read_excel(uploaded_file)
    df.columns = [c.strip() for c in df.columns]
    required = {"vendor_id", "vendor_name", "category_name"}
    missing = required - set(df.columns)
    if missing:
        st.error(f"Columnas faltantes: {', '.join(missing)}")
        return pd.DataFrame()
    return df


def df_to_mqls(df: pd.DataFrame, excluded_cats: list[str]) -> tuple[list[dict], int, int]:
    """Returns (mqls_to_assign, n_excluded_cats, n_already_assigned)."""
    all_mqls = []
    for _, row in df.iterrows():
        if not row.get("vendor_id") or not row.get("category_name"):
            continue
        salesrep = row.get("vendor_salesrep")
        already = bool(pd.notna(salesrep) and str(salesrep).strip())
        all_mqls.append({
            "vendor_id": str(row.get("vendor_id", "")),
            "vendor_name": str(row.get("vendor_name", "")),
            "category": str(row.get("category_name", "")),
            "region": str(row.get("region_name", "")),
            "phone": str(row.get("phone_number", "")),
            "mail": str(row.get("mail", "")),
            "already_assigned": already,
        })
    excluded_set = set(excluded_cats)
    already_list = [m for m in all_mqls if m["already_assigned"]]
    keep = [m for m in all_mqls if not m["already_assigned"] and m["category"] not in excluded_set]
    n_excluded_cats = len(all_mqls) - len(already_list) - len(keep)
    for m in keep:
        m.pop("already_assigned", None)
    return keep, n_excluded_cats, len(already_list)


# ══════════════════════════════════════════════════════════════
# PAGE: SUBIR SIGN-UPS
# ══════════════════════════════════════════════════════════════
if page == "📤 Subir sign-ups del día":
    st.header("Subir sign-ups del día")

    reps = get_reps()
    excluded_cats = get_excluded_cats()

    col1, col2 = st.columns([1, 2])

    with col1:
        today = st.date_input("Fecha", value=date.today(), format="DD/MM/YYYY")
        st.markdown("**¿Quién trabajó hoy?**")
        present_reps = [r for r in reps if st.checkbox(r, value=True, key=f"att_{r}")]

    with col2:
        uploaded = st.file_uploader(
            "Archivo de sign-ups del día",
            type=["xlsx", "xls", "csv"],
            help="Columnas requeridas: vendor_id, vendor_name, category_name",
        )
        df_raw = pd.DataFrame()
        if uploaded:
            df_raw = parse_upload(uploaded)
            if not df_raw.empty:
                mqls_preview, n_excl, n_assigned = df_to_mqls(df_raw, excluded_cats)
                msg = f"✓ {len(df_raw)} sign-ups · {df_raw['category_name'].nunique()} categorías"
                if n_assigned:
                    msg += f" · **{n_assigned} ya asignados** (descartados)"
                if n_excl:
                    msg += f" · **{n_excl} descartados** (categorías excluidas)"
                st.success(msg)
                if excluded_cats:
                    st.caption(f"Categorías excluidas: {', '.join(excluded_cats)}")
                with st.expander("Vista previa", expanded=False):
                    preview = df_raw[["vendor_id", "vendor_name", "category_name"]].copy()
                    if "region_name" in df_raw.columns:
                        preview["region_name"] = df_raw["region_name"]
                    if "vendor_salesrep" in df_raw.columns:
                        preview["ya_asignado"] = df_raw["vendor_salesrep"].apply(
                            lambda x: "✓" if pd.notna(x) and str(x).strip() else ""
                        )
                    preview["excluido"] = preview["category_name"].isin(excluded_cats).apply(
                        lambda x: "✓" if x else ""
                    )
                    st.dataframe(preview.head(30), use_container_width=True, hide_index=True)

    st.divider()

    if st.button("⚡ Distribuir y guardar", type="primary", use_container_width=True):
        if not present_reps:
            st.error("Selecciona al menos un rep.")
        elif df_raw.empty:
            st.error("Carga un archivo de sign-ups primero.")
        else:
            date_str = today.strftime("%Y-%m-%d")
            month_str = today.strftime("%Y-%m")

            with st.spinner("Distribuyendo..."):
                already_exists = sh.date_already_exists(SPREADSHEET_ID, date_str)
                if already_exists:
                    st.warning(f"Ya hay sign-ups para {date_str}. Se sobrescribirán.")
                    prev_assigned = sh.load_assignments(SPREADSHEET_ID)
                    if not prev_assigned.empty and "date" in prev_assigned.columns:
                        prev_assigned = prev_assigned[prev_assigned["date"] == date_str]
                        prev_present = prev_assigned["assigned_rep"].unique().tolist()
                    else:
                        prev_present = []
                    sh.delete_date(SPREADSHEET_ID, date_str)
                    for r in prev_present:
                        sh.decrement_day(SPREADSHEET_ID, month_str, r)

                processed_dates = sh.get_processed_dates(SPREADSHEET_ID, month_str)
                day_offset = len(processed_dates) % max(len(present_reps), 1)

                mqls, n_discarded_cats, n_already_assigned = df_to_mqls(df_raw, excluded_cats)

                if not mqls:
                    st.error("No quedan sign-ups para distribuir después de aplicar los filtros.")
                    st.stop()

                assigned = distribute(mqls, present_reps, day_offset)
                sh.save_assignments(SPREADSHEET_ID, assigned, date_str)
                for r in present_reps:
                    current_days = sh.load_days_worked(SPREADSHEET_ID, month_str).get(r, 0)
                    sh.upsert_days_worked(SPREADSHEET_ID, month_str, r, current_days + 1)

            report = fairness_report(assigned, present_reps)

            disc_txt = ""
            if n_already_assigned:
                disc_txt += f" · {n_already_assigned} ya asignados descartados"
            if n_discarded_cats:
                disc_txt += f" · {n_discarded_cats} excluidos por categoría"

            st.success(
                f"✅ {len(assigned)} sign-ups distribuidos{disc_txt} · "
                f"diff total ≤{report['total_diff']} · diff por categoría ≤{report['max_cat_diff']}"
            )

            cols = st.columns(len(present_reps))
            for i, r in enumerate(present_reps):
                cols[i].metric(r, report["by_rep"].get(r, 0))

            st.markdown("**Rep × Categoría**")
            matrix_rows = []
            for r in present_reps:
                row = {"Rep": r}
                for c in report["categories"]:
                    row[c] = report["by_rep_cat"][r].get(c, 0)
                row["Total"] = report["by_rep"].get(r, 0)
                matrix_rows.append(row)
            st.dataframe(pd.DataFrame(matrix_rows).set_index("Rep"), use_container_width=True)

            st.markdown("**Asignaciones completas**")
            result_df = pd.DataFrame(assigned)[[
                "assigned_rep", "vendor_id", "vendor_name", "category", "region", "phone", "mail"
            ]]
            result_df.columns = ["Rep", "ID", "Vendor", "Categoría", "Región", "Teléfono", "Email"]
            result_df = result_df.sort_values(["Rep", "Categoría"])
            st.dataframe(result_df, use_container_width=True, hide_index=True)

            csv = result_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Descargar CSV del día",
                data=csv,
                file_name=f"signups_{date_str}.csv",
                mime="text/csv",
            )


# ══════════════════════════════════════════════════════════════
# PAGE: HISTORIAL
# ══════════════════════════════════════════════════════════════
elif page == "📊 Historial del mes":
    st.header("Historial del mes")

    all_df = sh.load_assignments(SPREADSHEET_ID)

    if all_df.empty or "month" not in all_df.columns:
        st.info("Aún no hay datos guardados.")
    else:
        available_months = sorted(
            {m for m in all_df["month"].unique() if m},
            reverse=True,
        )
        if not available_months:
            st.info("Aún no hay datos guardados.")
        else:
            selected_month = st.selectbox("Mes", available_months)
            df_month = sh.load_assignments(SPREADSHEET_ID, selected_month)
            days_worked = sh.load_days_worked(SPREADSHEET_ID, selected_month)

            if df_month.empty:
                st.info("Sin datos para este mes.")
            else:
                all_reps = sorted(df_month["assigned_rep"].unique().tolist())
                all_dates = sorted(df_month["date"].unique().tolist())
                all_cats = sorted(df_month["category"].unique().tolist())

                c1, c2, c3 = st.columns(3)
                c1.metric("Sign-ups asignados", len(df_month))
                c2.metric("Días procesados", len(all_dates))
                c3.metric("Categorías", len(all_cats))

                st.markdown("**Resumen por rep**")
                summary_rows = []
                for r in all_reps:
                    rep_df = df_month[df_month["assigned_rep"] == r]
                    days = days_worked.get(r, 0)
                    summary_rows.append({
                        "Rep": r,
                        "Días trabajados": days,
                        "Sign-ups totales": len(rep_df),
                        "Sign-ups/día": round(len(rep_df) / days, 1) if days else "-",
                        "Categorías": len(rep_df["category"].unique()),
                    })
                st.dataframe(pd.DataFrame(summary_rows).set_index("Rep"), use_container_width=True)

                st.markdown("**Sign-ups por rep y categoría**")
                matrix_rows = []
                for r in all_reps:
                    rep_df = df_month[df_month["assigned_rep"] == r]
                    days = days_worked.get(r, 0)
                    row = {
                        "Rep": r,
                        "Días": days,
                        "Total": len(rep_df),
                        "x/día": round(len(rep_df) / days, 1) if days else "-",
                    }
                    for c in all_cats:
                        row[c] = len(rep_df[rep_df["category"] == c])
                    matrix_rows.append(row)

                totals_row = {"Rep": "TOTAL", "Días": "-", "Total": len(df_month), "x/día": "-"}
                for c in all_cats:
                    totals_row[c] = len(df_month[df_month["category"] == c])
                matrix_rows.append(totals_row)

                st.dataframe(pd.DataFrame(matrix_rows).set_index("Rep"), use_container_width=True)

                st.markdown("**Detalle por día**")
                for d in reversed(all_dates):
                    day_df = df_month[df_month["date"] == d]
                    by_rep = day_df.groupby("assigned_rep").size().to_dict()
                    summary = " · ".join(f"{r}: {n}" for r, n in sorted(by_rep.items()))
                    with st.expander(f"{d} — {len(day_df)} sign-ups  ({summary})"):
                        st.dataframe(
                            day_df[["assigned_rep", "vendor_name", "category", "region"]].rename(
                                columns={"assigned_rep": "Rep", "vendor_name": "Vendor",
                                         "category": "Categoría", "region": "Región"}
                            ).sort_values(["Rep", "Categoría"]),
                            use_container_width=True,
                            hide_index=True,
                        )

                csv = df_month.to_csv(index=False).encode("utf-8")
                st.download_button(
                    f"⬇️ Descargar CSV mes {selected_month}",
                    data=csv,
                    file_name=f"signups_{selected_month}.csv",
                    mime="text/csv",
                )


# ══════════════════════════════════════════════════════════════
# PAGE: CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════
elif page == "⚙️ Configuración":
    st.header("Configuración")

    reps = get_reps()
    excluded_cats = get_excluded_cats()

    st.subheader("Equipo de sales")
    st.caption("Los cambios aplican desde el siguiente día distribuido.")

    for i, r in enumerate(reps):
        c1, c2, c3 = st.columns([3, 1, 1])
        new_name = c1.text_input("Nombre", value=r, key=f"rep_name_{i}", label_visibility="collapsed")
        if c2.button("Guardar", key=f"rep_save_{i}"):
            updated = reps.copy()
            updated[i] = new_name.strip()
            sh.save_reps(SPREADSHEET_ID, updated)
            st.cache_data.clear()
            st.success(f"Rep actualizado a '{new_name.strip()}'")
            st.rerun()
        if c3.button("🗑️", key=f"rep_del_{i}", help="Eliminar rep"):
            updated = [r2 for j, r2 in enumerate(reps) if j != i]
            sh.save_reps(SPREADSHEET_ID, updated)
            st.cache_data.clear()
            st.rerun()

    st.divider()
    with st.form("add_rep_form", clear_on_submit=True):
        new_rep = st.text_input("Añadir nuevo rep")
        if st.form_submit_button("➕ Añadir rep"):
            if new_rep.strip() and new_rep.strip() not in reps:
                sh.save_reps(SPREADSHEET_ID, reps + [new_rep.strip()])
                st.cache_data.clear()
                st.success(f"Rep '{new_rep.strip()}' añadido.")
                st.rerun()

    st.subheader("Categorías excluidas")
    st.caption("Los sign-ups de estas categorías se descartan y no se asignan a ningún rep.")

    if excluded_cats:
        for cat in excluded_cats:
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"- {cat}")
            if c2.button("Eliminar", key=f"excat_{cat}"):
                updated = [c for c in excluded_cats if c != cat]
                sh.save_excluded_cats(SPREADSHEET_ID, updated)
                st.cache_data.clear()
                st.rerun()
    else:
        st.info("No hay categorías excluidas.")

    st.divider()
    with st.form("add_cat_form", clear_on_submit=True):
        new_cat = st.text_input(
            "Añadir categoría a excluir",
            help="Debe coincidir exactamente con el valor en category_name del archivo",
        )
        if st.form_submit_button("➕ Excluir categoría"):
            if new_cat.strip() and new_cat.strip() not in excluded_cats:
                sh.save_excluded_cats(SPREADSHEET_ID, excluded_cats + [new_cat.strip()])
                st.cache_data.clear()
                st.success(f"Categoría '{new_cat.strip()}' excluida.")
                st.rerun()

    st.subheader("Google Sheet")
    st.code(f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")
