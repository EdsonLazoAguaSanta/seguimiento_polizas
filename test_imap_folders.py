import imaplib

IMAP_HOST = "outlook.office365.com"
IMAP_PORT = 993
IMAP_USER = "elazo@aguasanta.cl"
IMAP_PASS = "Edla.2025"

print("Intentando conectar...")
imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
print("Conectado, probando login...")
imap.login(IMAP_USER, IMAP_PASS)
print("LOGIN OK")
imap.logout()
