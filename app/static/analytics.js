(function () {
  const dataNode = document.getElementById("analytics-data");
  if (!dataNode) return;

  const data = JSON.parse(dataNode.textContent);
  drawLineChart("salesChart", data.daily_sales, {
    color: "#276749",
    fill: "rgba(39, 103, 73, 0.12)",
    empty: "Нет продаж за выбранный период",
  });
  drawLineChart("stockChart", data.stock_snapshots, {
    color: "#8a5a12",
    fill: "rgba(138, 90, 18, 0.12)",
    empty: "Нет снимков остатков за выбранный период",
  });
  if (document.getElementById("storeSalesChart")) {
    drawHorizontalBarChart("storeSalesChart", data.store_sales || [], {
      color: "#42576a",
      empty: "Нет продаж по магазинам за выбранный период",
    });
  }

  function drawLineChart(canvasId, rows, options) {
    const canvas = document.getElementById(canvasId);
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    const pad = { top: 18, right: 18, bottom: 42, left: 58 };
    clear(ctx, width, height);

    if (!rows.length) {
      drawEmpty(ctx, width, height, options.empty);
      return;
    }

    const values = rows.map((row) => Number(row.quantity || 0));
    const maxValue = Math.max(...values, 1);
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;

    drawAxes(ctx, pad, width, height, maxValue);
    ctx.beginPath();
    rows.forEach((row, index) => {
      const x = pad.left + (rows.length === 1 ? 0 : (index / (rows.length - 1)) * plotWidth);
      const y = pad.top + plotHeight - (Number(row.quantity || 0) / maxValue) * plotHeight;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = options.color;
    ctx.lineWidth = 3;
    ctx.stroke();

    ctx.lineTo(pad.left + plotWidth, pad.top + plotHeight);
    ctx.lineTo(pad.left, pad.top + plotHeight);
    ctx.closePath();
    ctx.fillStyle = options.fill;
    ctx.fill();

    drawDateLabels(ctx, rows, pad, width, height);
  }

  function drawAxes(ctx, pad, width, height, maxValue) {
    const bottom = height - pad.bottom;
    ctx.strokeStyle = "#dfe4dd";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, pad.top);
    ctx.lineTo(pad.left, bottom);
    ctx.lineTo(width - pad.right, bottom);
    ctx.stroke();

    ctx.fillStyle = "#697268";
    ctx.font = "12px Inter, system-ui, sans-serif";
    ctx.fillText("0", 24, bottom + 4);
    ctx.fillText(Math.round(maxValue).toString(), 18, pad.top + 4);
  }

  function drawDateLabels(ctx, rows, pad, width, height) {
    const labels = [rows[0], rows[Math.floor(rows.length / 2)], rows[rows.length - 1]].filter(Boolean);
    const positions = [pad.left, width / 2, width - pad.right - 80];
    ctx.fillStyle = "#697268";
    ctx.font = "12px Inter, system-ui, sans-serif";
    labels.forEach((row, index) => {
      ctx.fillText(formatDate(row.date), positions[index], height - 14);
    });
  }

  function drawHorizontalBarChart(canvasId, rows, options) {
    const canvas = document.getElementById(canvasId);
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    const visibleRows = rows.slice(0, 18);
    const pad = { top: 18, right: 72, bottom: 24, left: 220 };
    clear(ctx, width, height);

    if (!visibleRows.length) {
      drawEmpty(ctx, width, height, options.empty);
      return;
    }

    const values = visibleRows.map((row) => Number(row.quantity || 0));
    const maxValue = Math.max(...values, 1);
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const gap = 8;
    const barHeight = Math.max(12, (plotHeight - gap * (visibleRows.length - 1)) / visibleRows.length);

    drawHorizontalAxes(ctx, pad, width, height, maxValue);
    visibleRows.forEach((row, index) => {
      const value = Number(row.quantity || 0);
      const y = pad.top + index * (barHeight + gap);
      const barWidth = (value / maxValue) * plotWidth;
      ctx.fillStyle = options.color;
      ctx.fillRect(pad.left, y, barWidth, barHeight);

      ctx.fillStyle = "#172018";
      ctx.font = "13px Inter, system-ui, sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(fitLabel(ctx, row.store, pad.left - 18), pad.left - 12, y + barHeight * 0.7);

      ctx.textAlign = "left";
      ctx.fillText(Math.round(value).toString(), pad.left + barWidth + 8, y + barHeight * 0.7);
    });
    ctx.textAlign = "left";
  }

  function drawHorizontalAxes(ctx, pad, width, height, maxValue) {
    ctx.strokeStyle = "#dfe4dd";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, pad.top);
    ctx.lineTo(pad.left, height - pad.bottom);
    ctx.lineTo(width - pad.right, height - pad.bottom);
    ctx.stroke();

    ctx.fillStyle = "#697268";
    ctx.font = "12px Inter, system-ui, sans-serif";
    ctx.fillText("0", pad.left - 4, height - 6);
    ctx.fillText(Math.round(maxValue).toString(), width - pad.right - 20, height - 6);
  }

  function fitLabel(ctx, value, maxWidth) {
    const text = String(value || "");
    if (ctx.measureText(text).width <= maxWidth) return text;
    let candidate = text;
    while (candidate.length > 4 && ctx.measureText(`${candidate}...`).width > maxWidth) {
      candidate = candidate.slice(0, -1);
    }
    return `${candidate}...`;
  }

  function formatDate(value) {
    const parts = String(value).split("-");
    return parts.length === 3 ? `${parts[2]}.${parts[1]}` : value;
  }

  function clear(ctx, width, height) {
    ctx.clearRect(0, 0, width, height);
  }

  function drawEmpty(ctx, width, height, message) {
    ctx.fillStyle = "#697268";
    ctx.font = "15px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(message, width / 2, height / 2);
    ctx.textAlign = "left";
  }
})();
