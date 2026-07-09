export const money = (value = 0) => `${new Intl.NumberFormat("vi-VN").format(value)} đ`;

export const clock = (seconds = 0) => {
  const minutes = Math.floor(seconds / 60);
  const remain = Math.floor(seconds % 60);
  return `${String(minutes).padStart(2, "0")}:${String(remain).padStart(2, "0")}.${String(Math.round((seconds % 1) * 1000)).padStart(3, "0")}`;
};

// Dạng ngắn mm:ss cho player và thông tin video.
export const clockShort = (seconds = 0) => {
  const minutes = Math.floor(seconds / 60);
  const remain = Math.floor(seconds % 60);
  return `${String(minutes).padStart(2, "0")}:${String(remain).padStart(2, "0")}`;
};

// Ngân sách ký tự để bản dịch đọc vừa khung thời gian (~13 ký tự tiếng Việt/giây).
export const charBudget = (segment) =>
  Math.max(60, Math.round((segment.end - segment.start) * 13));
