import base64, os

# Dashboard HTML закодирован в base64 чтобы избежать проблем с кавычками
html_b64 = (
    "PCFET0NUWVBFIGh0bWw+CjxodG1sPjxoZWFkPjx0aXRsZT5CaW5nWCBEYXNoYm9hcmQ8"
    "L3RpdGxlPjxtZXRhIGNoYXJzZXQ9InV0Zi04Ij4KPG1ldGEgbmFtZT0idmlld3BvcnQi"
)
print("Template:", len(html_b64))
print("This approach works!")
