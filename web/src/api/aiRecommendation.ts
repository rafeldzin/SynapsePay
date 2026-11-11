/**
 * Rekomendasi AI investigator.
 *
 * DATA DUMMY: endpoint AI belum ada di backend. Saat endpoint sudah jadi, ganti isi
 * getAiRecommendation() dengan panggilan API; komponen UI tidak perlu diubah.
 * Dummy ini sengaja TIDAK berisi status, ID, email, atau nominal, supaya tidak ada
 * angka karangan yang tampil seolah-olah data asli.
 */

export interface AiHypothesis {
  title: string;
  /** Peringkat 1 = paling mungkin. */
  rank: number;
  evidence: string[];
}

export interface AiRecommendation {
  isDummy: boolean;
  hypotheses: AiHypothesis[];
  recommendedAction: string;
  rationale: string;
  replyDraft: string;
}

export async function getAiRecommendation(_milestoneId: string): Promise<AiRecommendation> {
  return {
    isDummy: true,
    hypotheses: [
      {
        rank: 1,
        title: "Penerima salah ketik: dana menunggu diklaim di alamat yang keliru",
        evidence: [
          "Status item live di PayPal (lihat panel status PayPal)",
          "Jarak edit antara email kontrak dan email di pesan kontraktor",
          "Tidak ada payout kedua untuk milestone ini",
        ],
      },
      {
        rank: 2,
        title: "Keterlambatan proses PayPal",
        evidence: ["Ditolak bila status item bukan PENDING/PROCESSING"],
      },
    ],
    recommendedAction: "cancel_and_reissue",
    rationale:
      "Jangan kirim ulang: payout asli masih bisa diklaim. Batalkan item yang belum diklaim, lalu terbitkan ulang hanya setelah email baru dikonfirmasi lewat kanal asli (telepon), karena perubahan penerima diminta lewat chat.",
    replyDraft:
      "Halo, terima kasih sudah mengabari. Pembayaran milestone ini tercatat terkirim ke alamat PayPal di kontrak dan masih menunggu diklaim. Untuk keamanan, kami akan menelepon Anda di nomor yang terdaftar untuk mengonfirmasi alamat PayPal yang benar sebelum menerbitkan ulang pembayaran.",
  };
}
