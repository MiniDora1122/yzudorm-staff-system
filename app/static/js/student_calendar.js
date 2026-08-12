document.addEventListener("DOMContentLoaded", () => {
  const app = document.getElementById("studentScheduleApp");
  if (!app || typeof FullCalendar === "undefined") return;

  const locations = JSON.parse(document.getElementById("studentLocationsJson").textContent);
  const calendarElement = document.getElementById("studentCalendar");
  const modal = new bootstrap.Modal(document.getElementById("studentShiftModal"));
  let visibleMonth = "";
  let laneSyncFrame = 0;
  let calendarResizeTimer = 0;
  let observedCalendarWidth = 0;

  const monthKey = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
  const englishMonth = (date) => new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric" }).format(date);

  const loadHours = async () => {
    if (!visibleMonth) return;
    try {
      const response = await fetch(`${app.dataset.hoursUrl}?month=${encodeURIComponent(visibleMonth)}`, {
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const payload = await response.json();
      document.getElementById("studentMonthHours").textContent = Number(payload.total_hours).toLocaleString("zh-TW");
      document.getElementById("studentHoursMonthLabel").textContent = `${payload.month} `;
      document.getElementById("studentMonthWage").textContent = Number(payload.gross_wage).toLocaleString("zh-TW");
      document.getElementById("studentHourlyWage").textContent = Number(payload.hourly_wage).toLocaleString("zh-TW");
    } catch (_error) {
      // The calendar remains usable even if the summary request temporarily fails.
    }
  };

  const removeLocationColumn = () => {
    document.querySelectorAll("#studentCalendar .location-column-header, #studentCalendar .location-column-cell").forEach((element) => element.remove());
  };

  const syncLaneHeights = () => {
    if (calendar.view.type !== "dayGridMonth") return;
    calendarElement.querySelectorAll(".fc-daygrid-body tr").forEach((row) => {
      const spacer = row.querySelector(".location-day-number-spacer");
      const dayTopHeight = Math.max(
        0,
        ...[...row.querySelectorAll(".fc-daygrid-day-top")].map((element) => element.getBoundingClientRect().height),
      );
      if (spacer) spacer.style.height = `${dayTopHeight}px`;
      locations.forEach((location) => {
        const lanes = [...row.querySelectorAll(`.location-lane[data-location-id="${location.id}"]`)];
        const label = row.querySelector(`.location-row-label[data-location-id="${location.id}"]`);
        lanes.forEach((lane) => { lane.style.height = "auto"; });
        if (label) label.style.height = "auto";
        const height = Math.max(54, label?.scrollHeight || 0, ...lanes.map((lane) => lane.scrollHeight));
        lanes.forEach((lane) => { lane.style.height = `${height}px`; });
        if (label) label.style.height = `${height}px`;
      });
    });
  };

  const scheduleLaneSync = () => {
    window.cancelAnimationFrame(laneSyncFrame);
    laneSyncFrame = window.requestAnimationFrame(() => {
      laneSyncFrame = window.requestAnimationFrame(syncLaneHeights);
    });
  };

  const placeEventsInLocationLanes = () => {
    if (calendar.view.type !== "dayGridMonth") return;
    calendarElement.querySelectorAll(".fc-daygrid-event[data-location-id]").forEach((eventElement) => {
      const day = eventElement.closest(".fc-daygrid-day");
      const lane = day?.querySelector(`.location-lane[data-location-id="${eventElement.dataset.locationId}"]`);
      const harness = eventElement.closest(".fc-daygrid-event-harness") || eventElement;
      if (lane && harness.parentElement !== lane) lane.append(harness);
    });
    scheduleLaneSync();
  };

  const scheduleEventPlacement = () => {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(placeEventsInLocationLanes);
    });
    window.setTimeout(placeEventsInLocationLanes, 80);
  };

  const injectLocationColumn = () => {
    removeLocationColumn();
    if (calendar.view.type !== "dayGridMonth") return;
    const headerRow = calendarElement.querySelector(".fc-col-header tr");
    if (headerRow) {
      const header = document.createElement("th");
      header.className = "location-column-header";
      header.scope = "col";
      header.textContent = "地點";
      header.dataset.en = "Location";
      headerRow.prepend(header);
    }
    calendarElement.querySelectorAll(".fc-daygrid-body tr").forEach((row) => {
      const cell = document.createElement("td");
      cell.className = "location-column-cell";
      const spacer = document.createElement("div");
      spacer.className = "location-day-number-spacer";
      cell.append(spacer);
      locations.forEach((location) => {
        const label = document.createElement("div");
        label.className = "location-row-label";
        label.dataset.locationId = location.id;
        label.dataset.locationCode = location.code;
        label.style.setProperty("--location-color", location.color);
        const chinese = document.createElement("span");
        chinese.textContent = location.name;
        const english = document.createElement("small");
        english.lang = "en";
        english.textContent = location.nameEn || location.code;
        label.append(chinese, english);
        cell.append(label);
      });
      row.prepend(cell);
    });
    scheduleLaneSync();
    scheduleEventPlacement();
    window.setTimeout(scheduleLaneSync, 120);
  };

  const calendar = new FullCalendar.Calendar(calendarElement, {
    initialView: "dayGridMonth",
    locale: "zh-tw",
    firstDay: 0,
    height: "auto",
    dayMaxEvents: false,
    displayEventTime: false,
    headerToolbar: { left: "prev,next today", center: "title", right: "dayGridMonth,listMonth" },
    buttonText: { today: "今天", month: "月曆", list: "清單" },
    dayHeaderContent: (info) => {
      const wrapper = document.createElement("span");
      const chinese = document.createElement("span");
      chinese.textContent = new Intl.DateTimeFormat("zh-TW", { weekday: "short" }).format(info.date);
      const english = document.createElement("small");
      english.lang = "en";
      english.textContent = new Intl.DateTimeFormat("en-US", { weekday: "short" }).format(info.date);
      wrapper.append(chinese, english);
      return { domNodes: [wrapper] };
    },
    dayCellDidMount: (info) => {
      const eventsContainer = info.el.querySelector(".fc-daygrid-day-events");
      if (!eventsContainer || eventsContainer.querySelector(".location-lanes")) return;
      const wrapper = document.createElement("div");
      wrapper.className = "location-lanes";
      locations.forEach((location) => {
        const lane = document.createElement("div");
        lane.className = "location-lane";
        lane.dataset.locationId = location.id;
        lane.dataset.locationCode = location.code;
        lane.style.setProperty("--location-color", location.color);
        wrapper.append(lane);
      });
      eventsContainer.append(wrapper);
    },
    eventContent: (info) => {
      const props = info.event.extendedProps;
      const wrapper = document.createElement("div");
      wrapper.className = "calendar-event-content";
      if (info.view.type === "listMonth") {
        const chinese = document.createElement("span");
        chinese.textContent = `${props.locationLabel}｜${props.shiftTypeName}｜${props.timeLabel}`;
        const english = document.createElement("small");
        english.lang = "en";
        english.textContent = `${props.locationLabelEn} | ${props.shiftTypeNameEn}`;
        wrapper.append(chinese, english);
      } else {
        const time = document.createElement("span");
        time.className = "calendar-event-time";
        time.textContent = props.timeLabel;
        const type = document.createElement("strong");
        type.textContent = props.shiftTypeName;
        wrapper.append(time, type);
      }
      (props.workflowAnnotations || []).forEach((annotation) => {
        const badge = document.createElement("span");
        badge.className = `calendar-workflow-badge workflow-${annotation.class}`;
        badge.textContent = annotation.label;
        wrapper.append(badge);
      });
      return { domNodes: [wrapper] };
    },
    eventDidMount: (info) => {
      const props = info.event.extendedProps;
      const workflowText = (props.workflowAnnotations || []).map((item) => item.label).join("、");
      info.el.title = `${props.locationLabel}｜${props.shiftTypeName}｜${props.timeLabel}${workflowText ? `｜${workflowText}` : ""}`;
      info.el.dataset.locationId = String(props.locationId);
      info.el.dataset.locationCode = props.location;
      if (info.view.type !== "dayGridMonth") return;
      const day = info.el.closest(".fc-daygrid-day");
      const lane = day?.querySelector(`.location-lane[data-location-id="${props.locationId}"]`);
      const harness = info.el.closest(".fc-daygrid-event-harness") || info.el;
      if (lane) lane.append(harness);
      scheduleEventPlacement();
    },
    events: app.dataset.eventsUrl,
    datesSet: (info) => {
      visibleMonth = monthKey(info.view.currentStart);
      window.setTimeout(() => {
        const title = calendarElement.querySelector(".fc-toolbar-title");
        if (title) title.dataset.en = englishMonth(info.view.currentStart);
      }, 0);
      window.setTimeout(injectLocationColumn, 20);
      loadHours();
    },
    eventsSet: scheduleEventPlacement,
    eventClick: (info) => {
      const props = info.event.extendedProps;
      document.getElementById("studentShiftDate").textContent = props.shiftDate;
      document.getElementById("studentShiftLocation").textContent = props.locationLabel;
      document.getElementById("studentShiftLocationEn").textContent = props.locationLabelEn;
      document.getElementById("studentShiftType").textContent = props.shiftTypeName;
      document.getElementById("studentShiftTypeEn").textContent = props.shiftTypeNameEn;
      document.getElementById("studentShiftTime").textContent = props.timeLabel;
      document.getElementById("studentShiftHours").textContent = `${props.hours} 小時`;
      document.getElementById("studentShiftWorkflow").textContent =
        (props.workflowAnnotations || []).map((item) => item.label).join("、") || "無";
      modal.show();
    },
  });

  calendar.render();
  const refreshResponsiveCalendarLayout = () => {
    scheduleLaneSync();
    window.clearTimeout(calendarResizeTimer);
    calendarResizeTimer = window.setTimeout(() => {
      calendar.updateSize();
      injectLocationColumn();
      scheduleEventPlacement();
      window.setTimeout(scheduleLaneSync, 120);
    }, 160);
  };
  window.addEventListener("resize", refreshResponsiveCalendarLayout);
  if (typeof ResizeObserver !== "undefined") {
    const calendarResizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width || 0;
      if (Math.abs(width - observedCalendarWidth) < 1) return;
      observedCalendarWidth = width;
      refreshResponsiveCalendarLayout();
    });
    calendarResizeObserver.observe(calendarElement.parentElement);
  }
  if (document.fonts?.ready) document.fonts.ready.then(scheduleLaneSync);
});
