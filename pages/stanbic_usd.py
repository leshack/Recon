import io
import streamlit as st
import pandas as pd
import numpy as np
import re
import pdfplumber
from datetime import timedelta
from business_logic.auto import is_valid_csv_file_format, is_valid_excel_file_format, is_valid_pdf_file_format, InvalidFileFormatError, pdf_cleaner

def longest_common_substring(s1, s2):
    """Finds the longest continuous matching substring (word sequence) between two texts."""
    words1 = str(s1).lower().split()
    words2 = str(s2).lower().split()

    len1, len2 = len(words1), len(words2)
    dp = [[0] * (len2 + 1) for _ in range(len1 + 1)]

    longest_match = 0

    for i in range(1, len1 + 1):
        for j in range(1, len2 + 1):
            if words1[i - 1] == words2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
                longest_match = max(longest_match, dp[i][j])

    return longest_match

def regex_match_percentage(erp_text, bank_text):
    """Calculates the match percentage based on the longest common continuous word sequence."""
    if pd.isna(erp_text) or pd.isna(bank_text):
        return 0

    words_erp = str(erp_text).lower().split()

    if not words_erp:
        return 0

    longest_match_length = longest_common_substring(erp_text, bank_text)
    match_percentage = (longest_match_length / len(words_erp)) * 100

    return round(match_percentage, 2)

def parse_stanbic_usd_pdf(uploaded_file):
    """
    Parse all pages of a Stanbic USD bank statement PDF into a DataFrame.

    Uses text extraction (not table extraction) because pdfplumber's extract_table()
    collapses multi-page table continuations into single cells on pages without headers.
    Debit vs credit is resolved via the balance delta on each row.
    """
    AMT_RE = re.compile(r'(?<![A-Za-z\d,])-?[\d,]+\.\d{2}')
    DATE_RE = re.compile(r'^\d{2}/\d{2}/\d{4}')
    FOOTER_RE = re.compile(
        r'^(disclaimer|please\s+verify|please\s+note|summary\s+of\s+transactions'
        r'|printed\s+\d|computer\s+generated|page\s+\d)',
        re.IGNORECASE
    )
    SKIP_DESC = {'opening balance', 'closing balance', 'interim balance'}

    all_blocks = []

    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=3, y_tolerance=3)
            if not text:
                continue
            current = None
            for raw_line in text.split('\n'):
                line = raw_line.strip()
                if not line:
                    continue
                if FOOTER_RE.match(line):
                    if current is not None:
                        all_blocks.append(current)
                        current = None
                    continue
                if DATE_RE.match(line):
                    if current is not None:
                        all_blocks.append(current)
                    current = [line]
                elif current is not None:
                    current.append(line)
            if current is not None:
                all_blocks.append(current)

    rows = []
    prev_balance = None

    for block in all_blocks:
        if not block:
            continue

        first = block[0]
        trans_date = first[:10]
        rest = first[10:].strip()

        if DATE_RE.match(rest):
            rest = rest[10:].strip()

        full_text = rest + ' ' + ' '.join(block[1:])
        all_amounts = AMT_RE.findall(full_text)

        desc_parts = [AMT_RE.sub('', rest).strip()]
        for line in block[1:]:
            part = AMT_RE.sub('', line).strip()
            if part:
                desc_parts.append(part)
        description = re.sub(r'\s+', ' ', ' '.join(p for p in desc_parts if p)).strip()

        if description.lower() in SKIP_DESC:
            if all_amounts:
                try:
                    prev_balance = float(all_amounts[-1].replace(',', ''))
                except ValueError:
                    pass
            continue

        if len(all_amounts) < 2:
            continue

        try:
            balance = float(all_amounts[-1].replace(',', ''))
            tx_sum = sum(float(a.replace(',', '')) for a in all_amounts[:-1])
        except ValueError:
            continue

        debit = 0.0
        credit = 0.0
        if prev_balance is not None:
            delta = balance - prev_balance
            if delta < -0.005:
                debit = round(abs(tx_sum), 2)
            elif delta > 0.005:
                credit = round(abs(tx_sum), 2)

        prev_balance = balance

        rows.append({
            " Transaction Date ": trans_date,
            "Transaction Description": description,
            "Debit": debit,
            "Credit": credit,
        })

    if not rows:
        return pd.DataFrame(columns=[" Transaction Date ", "Transaction Description", "Debit", "Credit"])

    df = pd.DataFrame(rows)
    df[" Transaction Date "] = pd.to_datetime(df[" Transaction Date "], format="%d/%m/%Y")
    return df

st.title("🏦 Stanbic Reconciliation (USD)")

# First file uploader with a unique key
bank_statement = st.file_uploader("⬆️ Upload Bank Statement (PDF)", type=[".pdf"], key="bank_statement_usd")

# Second file uploader with a unique key
erp_transactions = st.file_uploader("⬆️ Upload BRS File", type=[".xls", ".xlsx"], key="erp_transactions_usd")

# Process files if uploaded
if bank_statement:
    st.success("Bank Statement successfully uploaded!")
    with st.expander("Below is the uploaded Bank Statement", expanded=False, icon="🔽"):
        st.write(f"Bank Statement File: {bank_statement.name}")
        bank_statement = parse_stanbic_usd_pdf(bank_statement)
        st.write(bank_statement)

if erp_transactions:
    st.success("BRS file successfully uploaded!")
    with st.expander("Below is the uploaded BRS report", expanded=False, icon="🔽"):
        st.write(f"BRS File: {erp_transactions.name}")
        erp_transactions = pd.read_excel(erp_transactions)
        erp_transactions["VOUCHER_DATE"] = pd.to_datetime(erp_transactions["VOUCHER_DATE"], format="%d/%m/%Y")
        st.write(erp_transactions)

# Divider to act as a separator
st.divider()

def reconciler(erp_file, bank_file, match_scale):
    if erp_file is not None and bank_file is not None:
        # Check if there are any nonzero values in the 'Credit' and 'Debit' columns
        if (bank_file["Credit"] != 0).any() and (bank_file["Debit"] != 0).any():
            print("It works!")

            # Add Match_Amount column in bank_file to dynamically pick Credit or Debit
            bank_file["Match_Amount"] = np.where(bank_file["Credit"] != 0, bank_file["Credit"], bank_file["Debit"])

            # Perform the merge (inner join with potential matches)
            merged_df = erp_file.merge(
                bank_file[[" Transaction Date ", "Credit", "Debit", "Transaction Description", "Match_Amount"]],
                left_on=["VOUCHER_DATE", "AMOUNT_SPECIFIC"],
                right_on=[" Transaction Date ", "Match_Amount"],
                how="inner",
                suffixes=("_ERP", "_BANK")
            )

            # Apply regex match percentage on both NARRATION and ENTITY_NAME
            merged_df["Regex_Match_Percentage"] = merged_df.apply(
                lambda row: max(
                    regex_match_percentage(row["NARRATION"], row["Transaction Description"]),
                    regex_match_percentage(row["ENTITY_NAME"], row["Transaction Description"])
                ),
                axis=1
            )

            filtered_df = merged_df[merged_df["Regex_Match_Percentage"] >= match_scale].reset_index(drop=True)

            # Remove matched transactions from bank_file
            unmatched_df = bank_file[
                ~bank_file[[" Transaction Date ", "Match_Amount", "Transaction Description"]].apply(tuple, axis=1).isin(
                    filtered_df[[" Transaction Date ", "Match_Amount", "Transaction Description"]].apply(tuple, axis=1)
                )
            ].reset_index(drop=True)

            # ---- CHEQUE MATCHING: extend date window ±3 days ----
            cheque_unmatched = unmatched_df[unmatched_df["Transaction Description"].str.contains("CHEQUE", case=False, na=False)]

            extended_matches = []

            for _, bank_row in cheque_unmatched.iterrows():
                date_range = (
                    bank_row[" Transaction Date "] - timedelta(days=3),
                    bank_row[" Transaction Date "] + timedelta(days=3)
                )

                candidates = erp_file[
                    (erp_file["AMOUNT_SPECIFIC"] == bank_row["Match_Amount"]) &
                    (erp_file["VOUCHER_DATE"].between(*date_range))
                ]

                for _, erp_row in candidates.iterrows():
                    score = max(
                        regex_match_percentage(erp_row["NARRATION"], bank_row["Transaction Description"]),
                        regex_match_percentage(erp_row["ENTITY_NAME"], bank_row["Transaction Description"])
                    )
                    if score >= match_scale:
                        extended_matches.append({**erp_row, **bank_row, "Regex_Match_Percentage": score})

            extended_df = pd.DataFrame(extended_matches)

            # Combine direct + extended matches, deduplicate on VOUCHER_NO
            final_matched = pd.concat([filtered_df, extended_df], ignore_index=True)
            final_matched = final_matched.drop_duplicates(subset=["VOUCHER_NO"], keep="first").reset_index(drop=True)

            # Recalculate truly unmatched bank rows
            final_matched_tuples = final_matched[[" Transaction Date ", "Match_Amount", "Transaction Description"]].apply(tuple, axis=1)
            final_unmatched_bank = bank_file[
                ~bank_file[[" Transaction Date ", "Match_Amount", "Transaction Description"]].apply(tuple, axis=1).isin(final_matched_tuples)
            ].reset_index(drop=True)

            # ERP rows with no match in final_matched
            matched_vouchers = set(final_matched["VOUCHER_NO"].dropna())
            final_unmatched_erp = erp_file[
                ~erp_file["VOUCHER_NO"].isin(matched_vouchers)
            ].reset_index(drop=True)

            # ---- Helper: format a BRS-column subset for download ----
            BRS_COLS = [
                "VOUCHER_NO", "VOUCHER_DATE", "INSTRUMENT_NO",
                "INSTRUMENT_STATUS_DATE", "INSTRUMENT_DATE",
                "AMOUNT_SPECIFIC", "NARRATION", "ENTITY_NAME",
                "INSTRUMENT_TYPE", "STATUS", "BANK_NAME",
                "VOUCHER_TYPE", "AMT_TYPE",
            ]

            def to_brs_export(df):
                cols = [c for c in BRS_COLS if c in df.columns]
                out = df[cols].copy()
                if "VOUCHER_DATE" in out.columns:
                    out["VOUCHER_DATE"] = pd.to_datetime(out["VOUCHER_DATE"]).dt.strftime("%d/%m/%Y")
                if "AMOUNT_SPECIFIC" in out.columns:
                    out["AMOUNT_SPECIFIC"] = (
                        pd.to_numeric(
                            out["AMOUNT_SPECIFIC"].astype(str).str.replace(",", "").str.strip(),
                            errors="coerce"
                        )
                        .fillna(0)
                        .round(0)
                        .astype(int)
                    )
                return out

            def to_xls_bytes(export_df, sheet_name):
                import xlwt
                buf = io.BytesIO()
                wb = xlwt.Workbook(encoding="utf-8")
                ws = wb.add_sheet(sheet_name[:31])
                for col_idx, col_name in enumerate(export_df.columns):
                    ws.write(0, col_idx, str(col_name))
                for row_idx, (_, row) in enumerate(export_df.iterrows(), start=1):
                    for col_idx, value in enumerate(row):
                        if pd.isna(value):
                            ws.write(row_idx, col_idx, "")
                        elif isinstance(value, (int, float, np.integer, np.floating)):
                            ws.write(row_idx, col_idx, value)
                        else:
                            ws.write(row_idx, col_idx, str(value))
                wb.save(buf)
                buf.seek(0)
                return buf.getvalue()

            matched_xls = to_xls_bytes(to_brs_export(final_matched), "Matched BRS")

            # ---- Unmatched ERP download (.xlsx) ----
            unmatched_erp_export = to_brs_export(final_unmatched_erp)
            unmatched_buf = io.BytesIO()
            with pd.ExcelWriter(unmatched_buf, engine="openpyxl") as writer:
                unmatched_erp_export.to_excel(writer, index=False, sheet_name="Unmatched BRS")
            unmatched_buf.seek(0)

            st.markdown("### ✅ Matched Transactions")
            st.write(final_matched)
            st.download_button(
                label="⬇️ Download Matched BRS",
                data=matched_xls,
                file_name="matched_brs_usd.xls",
                mime="application/vnd.ms-excel",
            )

            st.markdown("### ❌ Unmatched BRS Entries")
            st.write(final_unmatched_erp)
            st.download_button(
                label="⬇️ Download Unmatched BRS",
                data=unmatched_buf.getvalue(),
                file_name="unmatched_brs_usd.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

            st.markdown("### ❌ Unmatched Bank Statement Rows")
            st.write(final_unmatched_bank)

        else:
            print("No matching transactions found.")

    else:
        print("It doesn't work!")

if bank_statement is None or erp_transactions is None:
    st.markdown("## ⚠️ :red[Please upload both files (Bank Statement and BRS Report) to get started]")
else:
    st.markdown("### Adjust the slider to filter by the chosen % match.")
    match_scale = st.slider("Slide to select the % match", 0, 100)
    reconciler(erp_transactions, bank_statement, match_scale)
