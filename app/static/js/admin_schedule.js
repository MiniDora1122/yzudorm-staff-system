document.addEventListener("DOMContentLoaded", () => {
  const app = document.getElementById("scheduleApp");
  if (!app || typeof FullCalendar === "undefined") return;

  const locations = JSON.parse(document.getElementById("locationsData").textContent);
  const calendarElement = document.getElementById("adminCalendar");
  const locationFilter = document.getElementById("locationFilter");
  const staffFilter = document.getElementById("staffFilter");
  const form = document.getElementById("shiftForm");
  const modal = new bootstrap.Modal(document.getElementById("shiftModal"));
  const settingsModal = new bootstrap.Modal(document.getElementById("locationSettingsModal"));
  const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
  const formError = document.getElementById("shiftFormError");
  const settingsError = document.getElementById("settingsFormError");
  const saveButton = document.getElementById("saveShiftButton");
  const deleteButton = document.getElementById("deleteShiftButton");
  const deleteGroup = document.getElementById("deleteShiftGroup");
  const bulkToolbar = document.getElementById("bulkDeleteToolbar");
  const selectedShiftIds = new Set();
  let bulkDeleteMode = false;
  let editingSeriesId = null;
  let visibleMonth = "";
  let laneSyncFrame = 0;
  let calendarResizeTimer = 0;
  let observedCalendarWidth = 0;
  let editingLocationId = null;
  let editingShiftTypeId = null;

  const selectedLocation = () => document.querySelector('input[name="locationFilter"]:checked')?.value || "ALL";
  const selectedShiftType = () => document.querySelector('input[name="shiftTypeOption"]:checked');
  const localDateString = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  const monthKey = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
  const englishMonth = (date) => new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric" }).format(date);

  const showAlert = (message, category = "success") => {
    const alert = document.getElementById("scheduleAlert");
    alert.className = `alert alert-${category}`;
    alert.textContent = message;
    window.setTimeout(() => alert.classList.add("d-none"), 5000);
  };

  const showError = (element, message = "") => {
    element.textContent = message;
    element.classList.toggle("d-none", !message);
  };

  const refreshBulkSelection = () => {
    document.getElementById("bulkDeleteCount").textContent = selectedShiftIds.size;
    document.getElementById("bulkDeleteCountEn").textContent = selectedShiftIds.size;
    document.getElementById("confirmBulkDeleteButton").disabled = selectedShiftIds.size === 0;
    calendarElement.querySelectorAll(".fc-event").forEach((element) => {
      element.classList.toggle("bulk-shift-selected", selectedShiftIds.has(element.dataset.shiftId));
    });
  };

  const setBulkDeleteMode = (enabled) => {
    bulkDeleteMode = enabled;
    selectedShiftIds.clear();
    bulkToolbar.classList.toggle("d-none", !enabled);
    bulkToolbar.classList.toggle("d-flex", enabled);
    document.getElementById("toggleBulkDeleteButton").classList.toggle("active", enabled);
    calendarElement.classList.toggle("bulk-delete-mode", enabled);
    refreshBulkSelection();
  };

  const readError = async (response) => {
    try {
      const payload = await response.json();
      return payload.error?.message || "操作失敗，請稍後再試。";
    } catch (_error) {
      return "操作失敗，請重新整理後再試。";
    }
  };

  const setBusy = (busy) => {
    saveButton.disabled = busy;
    deleteButton.disabled = busy;
    saveButton.textContent = busy ? "儲存中…" : "儲存排班";
  };

  const filterShiftTypeCards = (code = "ALL") => {
    document.querySelectorAll("[data-shift-location]").forEach((button) => {
      button.classList.toggle("active", button.dataset.shiftLocation === code);
    });
    document.querySelectorAll("[data-shift-type-group]").forEach((group) => {
      group.classList.toggle("d-none", code !== "ALL" && group.dataset.shiftTypeGroup !== code);
    });
  };

  const renderHours = (payload) => {
    document.getElementById("hoursMonthLabel").textContent = `${payload.month} 時數`;
    document.getElementById("allHoursValue").textContent = Number(payload.total_hours).toLocaleString("zh-TW");
    const list = document.getElementById("hoursList");
    list.replaceChildren();
    payload.rows.forEach((row) => {
      const item = document.createElement("div");
      item.className = "hours-list-item";
      const header = document.createElement("div");
      header.className = "d-flex justify-content-between gap-2";
      const name = document.createElement("strong");
      name.textContent = row.name;
      const total = document.createElement("span");
      total.className = "badge text-bg-primary";
      total.textContent = `${row.total_hours} 小時`;
      header.append(name, total);
      const detail = document.createElement("div");
      detail.className = "small text-secondary mt-1 d-flex flex-wrap gap-x-2";
      const parts = locations
        .filter((location) => selectedLocation() === "ALL" || selectedLocation() === location.code)
        .map((location) => `${location.name} ${row.location_hours[location.code] || 0}`);
      detail.textContent = parts.join("｜");
      item.append(header, detail);
      list.append(item);
    });
  };

  const loadHours = async () => {
    if (!visibleMonth) return;
    const params = new URLSearchParams({ month: visibleMonth, location: selectedLocation() });
    if (staffFilter.value) params.set("staff_id", staffFilter.value);
    try {
      const response = await fetch(`${app.dataset.hoursUrl}?${params}`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(await readError(response));
      renderHours(await response.json());
    } catch (error) {
      showAlert(error.message, "danger");
    }
  };

  const removeLocationColumn = () => {
    document.querySelectorAll(".location-column-header, .location-column-cell").forEach((element) => element.remove());
  };

  const applyLocationVisibility = () => {
    const selected = selectedLocation();
    document.querySelectorAll(".location-lane, .location-row-label").forEach((element) => {
      element.classList.toggle("d-none", selected !== "ALL" && element.dataset.locationCode !== selected);
    });
  };

  const syncLaneHeights = () => {
    if (calendar.view.type !== "dayGridMonth") return;
    applyLocationVisibility();
    document.querySelectorAll(".fc-daygrid-body tr").forEach((row) => {
      const spacer = row.querySelector(".location-day-number-spacer");
      const dayTopHeight = Math.max(
        0,
        ...[...row.querySelectorAll(".fc-daygrid-day-top")].map((element) => element.getBoundingClientRect().height),
      );
      if (spacer) spacer.style.height = `${dayTopHeight}px`;
      locations.forEach((location) => {
        const lanes = [...row.querySelectorAll(`.location-lane[data-location-id="${location.id}"]:not(.d-none)`)];
        if (!lanes.length) return;
        const label = row.querySelector(`.location-row-label[data-location-id="${location.id}"]`);
        lanes.forEach((lane) => { lane.style.height = "auto"; });
        if (label) label.style.height = "auto";
        const height = Math.max(54, label?.scrollHeight || 0, ...lanes.map((lane) => lane.scrollHeight));
        lanes.forEach((lane) => { lane.style.height = `${height}px`; });
        if (label && !label.classList.contains("d-none")) label.style.height = `${height}px`;
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
    const headerRow = document.querySelector(".fc-col-header tr");
    if (headerRow) {
      const header = document.createElement("th");
      header.className = "location-column-header";
      header.scope = "col";
      header.textContent = "地點";
      header.dataset.en = "Location";
      headerRow.prepend(header);
    }
    document.querySelectorAll(".fc-daygrid-body tr").forEach((row) => {
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

  const openCreateModal = (dateValue = localDateString(new Date()), locationCode = "ALL") => {
    form.reset();
    editingSeriesId = null;
    document.getElementById("shiftId").value = "";
    document.getElementById("shiftDate").value = dateValue;
    document.getElementById("shiftPublication").value = "DRAFT";
    document.getElementById("recurrenceEnd").min = dateValue;
    document.getElementById("shiftModalTitle").textContent = "新增排班";
    document.getElementById("shiftModalTitle").dataset.en = "Add shift";
    deleteGroup.classList.add("d-none");
    document.getElementById("recurrencePanel").classList.remove("d-none");
    document.getElementById("recurrenceFields").classList.add("d-none");
    document.getElementById("recurrenceEnd").required = false;
    document.getElementById("continueAddWrapper").classList.remove("d-none");
    filterShiftTypeCards(locationCode);
    showError(formError);
    modal.show();
  };

  const openEditModal = (event) => {
    form.reset();
    const props = event.extendedProps;
    editingSeriesId = props.seriesId || null;
    document.getElementById("shiftId").value = event.id;
    document.getElementById("shiftDate").value = props.shiftDate;
    document.getElementById("shiftStaff").value = String(props.staffId);
    document.getElementById("shiftPublication").value = props.publicationStatus || "PUBLISHED";
    const radio = document.querySelector(`input[name="shiftTypeOption"][value="${props.shiftTypeId}"]`);
    if (radio) radio.checked = true;
    filterShiftTypeCards(props.location);
    document.getElementById("shiftModalTitle").textContent = "編輯排班";
    document.getElementById("shiftModalTitle").dataset.en = "Edit shift";
    deleteGroup.classList.remove("d-none");
    document.querySelectorAll(".series-delete-option").forEach((element) => element.classList.toggle("d-none", !editingSeriesId));
    document.getElementById("recurrencePanel").classList.add("d-none");
    document.getElementById("recurrenceEnd").required = false;
    document.getElementById("continueAddWrapper").classList.add("d-none");
    showError(formError);
    modal.show();
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
      if (info.view.type === "listMonth") {
        const date = document.createElement("strong");
        date.textContent = `${String(info.date.getMonth() + 1).padStart(2, "0")}/${String(info.date.getDate()).padStart(2, "0")}`;
        wrapper.append(date);
      }
      const chinese = document.createElement("span");
      chinese.textContent = `${info.view.type === "listMonth" ? " " : ""}${new Intl.DateTimeFormat("zh-TW", { weekday: "short" }).format(info.date)}`;
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
        chinese.textContent = `${props.isVacancy ? "缺員（原 " : ""}${props.staffName}${props.isVacancy ? "）" : ""}｜${props.locationLabel}｜${props.shiftTypeName}｜${props.timeLabel}`;
        const english = document.createElement("small");
        english.lang = "en";
        english.textContent = `${props.locationLabelEn} | ${props.shiftTypeNameEn}`;
        wrapper.append(chinese, english);
      } else {
        const time = document.createElement("span");
        time.className = "calendar-event-time";
        time.textContent = props.timeLabel;
        const staff = document.createElement("strong");
        staff.textContent = props.isVacancy ? `缺員｜原 ${props.staffName}` : props.staffName;
        wrapper.append(time, staff);
      }
      (props.workflowAnnotations || []).forEach((annotation) => {
        const badge = document.createElement("span");
        badge.className = `calendar-workflow-badge workflow-${annotation.class}`;
        badge.textContent = annotation.label;
        wrapper.append(badge);
      });
      if (props.isDraft) {
        const badge = document.createElement("span");
        badge.className = "calendar-workflow-badge workflow-warning";
        badge.textContent = "草稿 Draft";
        wrapper.append(badge);
      }
      return { domNodes: [wrapper] };
    },
    eventDidMount: (info) => {
      const props = info.event.extendedProps;
      const workflowText = (props.workflowAnnotations || []).map((item) => item.label).join("、");
      info.el.title = `${props.staffName}｜${props.locationLabel}｜${props.shiftTypeName}｜${props.timeLabel}${workflowText ? `｜${workflowText}` : ""}`;
      info.el.dataset.locationId = String(props.locationId);
      info.el.dataset.locationCode = props.location;
      info.el.dataset.shiftId = String(info.event.id);
      info.el.classList.toggle("bulk-shift-selected", selectedShiftIds.has(String(info.event.id)));
      if (info.view.type !== "dayGridMonth") return;
      const day = info.el.closest(".fc-daygrid-day");
      const lane = day?.querySelector(`.location-lane[data-location-id="${props.locationId}"]`);
      const harness = info.el.closest(".fc-daygrid-event-harness") || info.el;
      if (lane) lane.append(harness);
      scheduleEventPlacement();
    },
    events: async (info, success, failure) => {
      const params = new URLSearchParams({ start: info.startStr, end: info.endStr, location: selectedLocation() });
      if (staffFilter.value) params.set("staff_id", staffFilter.value);
      try {
        const response = await fetch(`${app.dataset.eventsUrl}?${params}`, { credentials: "same-origin" });
        if (!response.ok) throw new Error(await readError(response));
        success(await response.json());
      } catch (error) {
        showAlert(error.message, "danger");
        failure(error);
      }
    },
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
    dateClick: (info) => {
      const lane = info.jsEvent.target.closest(".location-lane");
      openCreateModal(info.dateStr, lane?.dataset.locationCode || selectedLocation());
    },
    eventClick: (info) => {
      if (bulkDeleteMode) {
        const id = String(info.event.id);
        if (selectedShiftIds.has(id)) selectedShiftIds.delete(id);
        else selectedShiftIds.add(id);
        refreshBulkSelection();
        return;
      }
      openEditModal(info.event);
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
  document.getElementById("addShiftButton").addEventListener("click", () => openCreateModal());
  document.getElementById("locationSettingsButton").addEventListener("click", () => settingsModal.show());
  document.getElementById("toggleBulkDeleteButton").addEventListener("click", () => setBulkDeleteMode(!bulkDeleteMode));
  document.getElementById("cancelBulkDeleteButton").addEventListener("click", () => setBulkDeleteMode(false));
  document.getElementById("confirmBulkDeleteButton").addEventListener("click", async () => {
    if (!selectedShiftIds.size || !window.confirm(`確定刪除已選取的 ${selectedShiftIds.size} 筆排班？\nDelete ${selectedShiftIds.size} selected shifts?`)) return;
    try {
      const response = await fetch(app.dataset.bulkDeleteUrl, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ shift_ids: [...selectedShiftIds] }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const payload = await response.json();
      setBulkDeleteMode(false);
      calendar.refetchEvents();
      loadHours();
      showAlert(`已刪除 ${payload.cancelled} 筆排班。 / ${payload.cancelled} shifts deleted.`, "warning");
    } catch (error) { showAlert(error.message, "danger"); }
  });
  document.getElementById("repeatWeekly").addEventListener("change", (event) => {
    const enabled = event.target.checked;
    document.getElementById("recurrenceFields").classList.toggle("d-none", !enabled);
    document.getElementById("recurrenceEnd").required = enabled;
    if (enabled && !document.getElementById("recurrenceEnd").value) {
      document.getElementById("recurrenceEnd").value = document.getElementById("shiftDate").value;
    }
  });
  document.getElementById("shiftDate").addEventListener("change", (event) => {
    document.getElementById("recurrenceEnd").min = event.target.value;
    if (document.getElementById("repeatWeekly").checked && document.getElementById("recurrenceEnd").value < event.target.value) {
      document.getElementById("recurrenceEnd").value = event.target.value;
    }
  });
  locationFilter.addEventListener("change", () => {
    applyLocationVisibility();
    calendar.refetchEvents();
    loadHours();
    scheduleLaneSync();
  });
  staffFilter.addEventListener("change", () => { calendar.refetchEvents(); loadHours(); });
  document.querySelectorAll("[data-shift-location]").forEach((button) => {
    button.addEventListener("click", () => filterShiftTypeCards(button.dataset.shiftLocation));
  });

  const resetLocationForm = () => {
    editingLocationId = null;
    document.getElementById("locationForm").reset();
    document.getElementById("locationColor").value = "#7c3aed";
    document.getElementById("locationFormTitle").textContent = "新增工作地點";
    document.getElementById("locationFormTitle").dataset.en = "Add work location";
    document.getElementById("saveLocationButton").textContent = "新增地點";
    document.getElementById("saveLocationButton").dataset.en = "Add location";
    document.getElementById("cancelLocationEdit").classList.add("d-none");
  };

  const resetShiftTypeForm = () => {
    editingShiftTypeId = null;
    document.getElementById("shiftTypeForm").reset();
    document.getElementById("shiftTypeFormTitle").textContent = "新增班別";
    document.getElementById("shiftTypeFormTitle").dataset.en = "Add shift type";
    document.getElementById("saveShiftTypeButton").textContent = "新增班別";
    document.getElementById("saveShiftTypeButton").dataset.en = "Add shift type";
    document.getElementById("cancelShiftTypeEdit").classList.add("d-none");
  };

  document.querySelectorAll(".edit-location-button").forEach((button) => {
    button.addEventListener("click", () => {
      editingLocationId = button.dataset.id;
      document.getElementById("locationName").value = button.dataset.name;
      document.getElementById("locationNameEn").value = button.dataset.nameEn;
      document.getElementById("locationCode").value = button.dataset.code;
      document.getElementById("locationColor").value = button.dataset.color;
      document.getElementById("locationFormTitle").textContent = "編輯工作地點";
      document.getElementById("locationFormTitle").dataset.en = "Edit work location";
      document.getElementById("saveLocationButton").textContent = "儲存地點";
      document.getElementById("saveLocationButton").dataset.en = "Save location";
      document.getElementById("cancelLocationEdit").classList.remove("d-none");
      document.getElementById("locationName").focus();
    });
  });
  document.getElementById("cancelLocationEdit").addEventListener("click", resetLocationForm);

  document.querySelectorAll(".edit-shift-type-button").forEach((button) => {
    button.addEventListener("click", () => {
      editingShiftTypeId = button.dataset.id;
      document.getElementById("newShiftLocation").value = button.dataset.locationId;
      document.getElementById("newShiftName").value = button.dataset.name;
      document.getElementById("newShiftNameEn").value = button.dataset.nameEn;
      document.getElementById("newShiftCode").value = button.dataset.code;
      document.getElementById("newShiftStart").value = button.dataset.start;
      document.getElementById("newShiftEnd").value = button.dataset.end;
      document.getElementById("newShiftHours").value = button.dataset.hours;
      document.getElementById("shiftTypeFormTitle").textContent = "編輯班別";
      document.getElementById("shiftTypeFormTitle").dataset.en = "Edit shift type";
      document.getElementById("saveShiftTypeButton").textContent = "儲存班別";
      document.getElementById("saveShiftTypeButton").dataset.en = "Save shift type";
      document.getElementById("cancelShiftTypeEdit").classList.remove("d-none");
      document.getElementById("newShiftName").focus();
    });
  });
  document.getElementById("cancelShiftTypeEdit").addEventListener("click", resetShiftTypeForm);

  document.querySelectorAll(".delete-location-button").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm(`確定刪除地點「${button.dataset.name}」？此地點的班別也會停止使用，歷史排班仍會保留。\nDelete this location and archive its shift types?`)) return;
      try {
        const response = await fetch(`${app.dataset.locationUrl}/${button.dataset.id}`, {
          method: "DELETE", credentials: "same-origin", headers: { "X-CSRFToken": csrfToken },
        });
        if (!response.ok) throw new Error(await readError(response));
        window.location.reload();
      } catch (error) { showError(settingsError, error.message); }
    });
  });

  document.querySelectorAll(".delete-shift-type-button").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm(`確定刪除班別「${button.dataset.name}」？歷史排班仍會保留。\nDelete this shift type?`)) return;
      try {
        const response = await fetch(`${app.dataset.shiftTypeUrl}/${button.dataset.id}`, {
          method: "DELETE", credentials: "same-origin", headers: { "X-CSRFToken": csrfToken },
        });
        if (!response.ok) throw new Error(await readError(response));
        window.location.reload();
      } catch (error) { showError(settingsError, error.message); }
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const typeRadio = selectedShiftType();
    if (!form.reportValidity() || !typeRadio) {
      showError(formError, "請選擇一個班別。");
      return;
    }
    const shiftId = document.getElementById("shiftId").value;
    const repeatWeekly = !shiftId && document.getElementById("repeatWeekly").checked;
    const keepAdding = !shiftId && document.getElementById("continueAdding").checked;
    const payload = {
      shift_date: document.getElementById("shiftDate").value,
      staff_id: document.getElementById("shiftStaff").value,
      shift_type_id: typeRadio.value,
      allow_location_overlap: false,
      repeat_weekly: repeatWeekly,
      recurrence_end: repeatWeekly ? document.getElementById("recurrenceEnd").value : null,
      publication_status: document.getElementById("shiftPublication").value,
    };
    setBusy(true);
    showError(formError);
    try {
      const saveRequest = () => fetch(shiftId ? `${app.dataset.createUrl}/${shiftId}` : app.dataset.createUrl, {
          method: shiftId ? "PUT" : "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
          body: JSON.stringify(payload),
        });
      let response = await saveRequest();
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        const code = errorPayload.error?.code;
        const message = errorPayload.error?.message || "操作失敗，請稍後再試。";
        if (response.status === 409 && code === "LOCATION_CONFIRM_REQUIRED") {
          const confirmed = window.confirm(`${message}\n\n仍要安排多人於同一地點的重疊時段嗎？\nContinue with multiple staff at this location and time?`);
          if (!confirmed) throw new Error("已取消儲存，未變更排班。 / Save cancelled; no schedule was changed.");
          payload.allow_location_overlap = true;
          response = await saveRequest();
          if (!response.ok) throw new Error(await readError(response));
        } else {
          throw new Error(message);
        }
      }
      if (!keepAdding) modal.hide();
      else {
        if (repeatWeekly) {
          document.getElementById("repeatWeekly").checked = false;
          document.getElementById("recurrenceFields").classList.add("d-none");
          document.getElementById("recurrenceEnd").required = false;
          document.getElementById("recurrenceEnd").value = "";
        }
        showError(formError, "");
      }
      calendar.refetchEvents();
      loadHours();
      showAlert(shiftId ? "排班已更新。" : repeatWeekly ? "每週重複排班系列已建立。 / Weekly recurring series created." : keepAdding ? "已新增，可繼續選擇日期或班別。" : "排班已新增。");
    } catch (error) {
      showError(formError, error.message);
    } finally {
      setBusy(false);
    }
  });

  document.querySelectorAll(".delete-scope-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const shiftId = document.getElementById("shiftId").value;
      const scope = button.dataset.scope;
      const labels = { single: "這一筆", future: "本筆及後續重複時段", series: "整個重複系列" };
      if (!shiftId || !window.confirm(`確定刪除${labels[scope]}排班？歷史稽核仍會保留。\nConfirm deletion of ${scope === "single" ? "this shift" : scope === "future" ? "this and following occurrences" : "the entire series"}?`)) return;
      setBusy(true);
      try {
        const response = await fetch(`${app.dataset.createUrl}/${shiftId}?scope=${scope}`, {
          method: "DELETE", credentials: "same-origin", headers: { "X-CSRFToken": csrfToken },
        });
        if (!response.ok) throw new Error(await readError(response));
        const payload = await response.json();
        modal.hide();
        calendar.refetchEvents();
        loadHours();
        showAlert(`已刪除 ${payload.cancelled} 筆排班。 / ${payload.cancelled} shifts deleted.`, "warning");
      } catch (error) { showError(formError, error.message); }
      finally { setBusy(false); }
    });
  });

  document.getElementById("locationForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    showError(settingsError);
    const payload = {
      name: document.getElementById("locationName").value,
      name_en: document.getElementById("locationNameEn").value,
      code: document.getElementById("locationCode").value,
      color: document.getElementById("locationColor").value,
    };
    try {
      const response = await fetch(editingLocationId ? `${app.dataset.locationUrl}/${editingLocationId}` : app.dataset.locationUrl, {
        method: editingLocationId ? "PUT" : "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await readError(response));
      window.location.reload();
    } catch (error) { showError(settingsError, error.message); }
  });

  document.getElementById("shiftTypeForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    showError(settingsError);
    const payload = {
      location_id: document.getElementById("newShiftLocation").value,
      name: document.getElementById("newShiftName").value,
      name_en: document.getElementById("newShiftNameEn").value,
      code: document.getElementById("newShiftCode").value,
      start_time: document.getElementById("newShiftStart").value,
      end_time: document.getElementById("newShiftEnd").value,
      default_hours: document.getElementById("newShiftHours").value,
    };
    try {
      const response = await fetch(editingShiftTypeId ? `${app.dataset.shiftTypeUrl}/${editingShiftTypeId}` : app.dataset.shiftTypeUrl, {
        method: editingShiftTypeId ? "PUT" : "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await readError(response));
      window.location.reload();
    } catch (error) { showError(settingsError, error.message); }
  });
});
