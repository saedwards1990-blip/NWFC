// NWFC Google Apps Script (v2): adds ticket-offer support and write locking.
// Replace ALL of the existing script with this, then Deploy > Manage deployments >
// (pencil) > Version: New version > Deploy. The Web App URL stays the same.

function out(text) {
  return ContentService.createTextOutput(text);
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(20000);
  } catch (err) {
    return out("ERROR: Server busy, please try again");
  }
  try {
    var action = e.parameter.action;
    var ss = SpreadsheetApp.getActiveSpreadsheet();

    // --- Connection / setup check for the ticket manager ---
    if (action === "ping_tickets") {
      var s = ss.getSheetByName("individuals");
      if (!s) return out("ERROR: individuals sheet not found");
      var h = s.getRange(1, 1, 1, s.getLastColumn()).getValues()[0].map(function (x) { return String(x).trim(); });
      if (h.indexOf("ticket_status") < 0) return out("ERROR: ticket_status column header is missing in the individuals tab");
      return out("SUCCESS");
    }

    // --- Set ticket_status for one or more competitors (matched by name + category) ---
    if (action === "set_ticket_status") {
      var sheet = ss.getSheetByName("individuals");
      if (!sheet) return out("ERROR: individuals sheet not found");
      var items;
      try {
        items = JSON.parse(e.parameter.items);
      } catch (err2) {
        return out("ERROR: could not read items");
      }
      var data = sheet.getDataRange().getValues();
      var headers = data[0].map(function (x) { return String(x).trim(); });
      var iName = headers.indexOf("name");
      var iCat = headers.indexOf("category");
      var iStat = headers.indexOf("ticket_status");
      if (iName < 0 || iCat < 0 || iStat < 0) return out("ERROR: name / category / ticket_status header missing");

      var plan = [];
      for (var k = 0; k < items.length; k++) {
        var n = String(items[k].name).trim().toLowerCase();
        var c = String(items[k].category).trim().toLowerCase();
        var rows = [];
        for (var r = 1; r < data.length; r++) {
          if (String(data[r][iName]).trim().toLowerCase() === n &&
              String(data[r][iCat]).trim().toLowerCase() === c) {
            rows.push(r);
          }
        }
        if (rows.length === 0) return out("ERROR: not found: " + items[k].name);
        if (rows.length > 1) return out("ERROR: duplicate rows for: " + items[k].name + " (fix the Sheet first)");
        plan.push({ row: rows[0] + 1, status: String(items[k].status || "") });
      }
      // Everything matched: now write.
      for (var m = 0; m < plan.length; m++) {
        sheet.getRange(plan[m].row, iStat + 1).setValue(plan[m].status);
      }
      return out("SUCCESS");
    }

    // --- Original behaviour: append an individual or relay result ---
    var sheetName = (action === "add_individual") ? "individuals" : "relays";
    var sht = ss.getSheetByName(sheetName);
    if (!sht) {
      return out("ERROR: Sheet not found");
    }
    var hdrs = sht.getRange(1, 1, 1, sht.getLastColumn()).getValues()[0];
    var nextId = sht.getLastRow();
    var newRow = hdrs.map(function (hd) {
      if (hd === "id") return nextId;
      return e.parameter[hd] || "";
    });
    sht.appendRow(newRow);
    return out("SUCCESS");
  } finally {
    lock.releaseLock();
  }
}
