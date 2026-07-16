import subprocess
import sys

def test_esp32():
    port = "/dev/cu.usbserial-0001"
    print(f"Mencoba terhubung ke ESP32 di port {port}...")
    try:

        print("Menjalankan esptool...")
        result = subprocess.run(
            ["python3", "-m", "esptool", "--port", port, "read_mac"],
            capture_output=True,
            text=True,
            check=True
        )
        print("\n--- BERHASIL TERHUBUNG ---")
        print("Output dari ESP32:")
        print(result.stdout)
        print("ESP32 Anda siap digunakan!")
        
    except subprocess.CalledProcessError as e:
        print("\n--- GAGAL TERHUBUNG ---")
        print("Error saat mencoba terhubung ke ESP32.")
        print(f"Pesan Error:\n{e.stderr}")
        print("\nSaran: ")
        print("1. Pastikan ESP32 terhubung dengan benar menggunakan kabel data USB (bukan sekadar kabel charger).")
        print("2. Jika Anda menggunakan board ESP32 tertentu, mungkin Anda perlu menekan dan menahan tombol 'BOOT' saat proses koneksi.")
    except FileNotFoundError:
        print("\n--- ERROR: esptool.py tidak ditemukan ---")
        print("Silakan install esptool terlebih dahulu dengan menjalankan:")
        print("pip3 install esptool")
    except Exception as e:
        print(f"\n--- ERROR TIDAK DIKENAL ---\n{e}")

if __name__ == "__main__":
    test_esp32()
