import streamlit as st
import subprocess

st.title("Booster CDT")

if st.button("Ejecutar cálculo"):
    resultado = subprocess.run(
        ["python", "booster_cdt.py"],
        capture_output=True,
        text=True
    )

    st.text(resultado.stdout)
