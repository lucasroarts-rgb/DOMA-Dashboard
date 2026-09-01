// Shared live status for the Team & Meetings checklist. Firestore is the
// single source of truth for an item's status once anyone has clicked it -
// both the local dashboard and the published GitHub Pages site read/write
// the same project, so a click from either place updates everyone's open
// tab within a second or two (via onSnapshot), no reload needed.
//
// No auth: Firestore rules are left open (read/write, no login required),
// matching this project's existing "client-side password gate is cosmetic,
// not real security" posture - see README.md. Don't add a secret key here
// expecting it to gate access; only Firestore Rules can actually do that.
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-app.js";
import { getFirestore, doc, setDoc, addDoc, deleteDoc, onSnapshot, collection } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-firestore.js";

const firebaseConfig = {
  apiKey: "AIzaSyAend14H6sI98DrjZvNfw-sfNYfEykaI6o",
  authDomain: "doma-dshboard.firebaseapp.com",
  projectId: "doma-dshboard",
  storageBucket: "doma-dshboard.firebasestorage.app",
  messagingSenderId: "355638755223",
  appId: "1:355638755223:web:317eeb9f36fa84e5df0ed2",
};

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);
const STATUS_COLLECTION = "team_action_item_status";
const MANUAL_ITEMS_COLLECTION = "team_manual_items";
const CALENDAR_ITEMS_COLLECTION = "content_calendar_items";
const CALENDAR_STATUS_COLLECTION = "content_calendar_item_status";
const LINKS_COLLECTION = "useful_links";
const COMMUNITY_STATS_COLLECTION = "community_stats";
const COMMUNITY_STATS_DOC_ID = "doma_free_community";

function watch(collectionName, onChange, mapEntry) {
  return onSnapshot(
    collection(db, collectionName),
    (snapshot) => onChange(snapshot, mapEntry),
    (error) => {
      // onSnapshot streams die permanently on error (e.g. a permission
      // problem at subscribe time) - log it loudly instead of silently
      // going stale, since a dead listener looks identical to "nobody
      // else has changed anything yet" from the UI.
      console.error(`Team live-sync (${collectionName}) stopped:`, error.code, error.message);
    }
  );
}

window.domaTeamSync = {
  async setStatus(itemId, status) {
    // merge:true - this doc may already hold field overrides from
    // updateActionItem() below; a plain (non-merge) setDoc would wipe them.
    await setDoc(doc(db, STATUS_COLLECTION, String(itemId)), { status, updated_at: Date.now() }, { merge: true });
  },
  subscribeAll(callback) {
    return watch(STATUS_COLLECTION, (snapshot) => {
      const statuses = new Map();
      snapshot.forEach((docSnap) => statuses.set(docSnap.id, docSnap.data().status));
      callback(statuses);
    });
  },
  // Meeting-derived tickets have no Firestore doc of their own (their text
  // is baked from SQLite/data.js at publish time) - editing one writes a
  // field override into the same per-item status doc, merged on top of the
  // baked text at render time. Deliberately reuses STATUS_COLLECTION rather
  // than a new collection: one doc per item id either way, and it's already
  // covered by the open Firestore rules.
  async updateActionItem(itemId, fields) {
    await setDoc(doc(db, STATUS_COLLECTION, String(itemId)), { ...fields, updated_at: Date.now() }, { merge: true });
  },
  subscribeOverrides(callback) {
    return watch(STATUS_COLLECTION, (snapshot) => {
      const overrides = new Map();
      snapshot.forEach((docSnap) => {
        const { status, updated_at, ...rest } = docSnap.data();
        if (Object.keys(rest).length) overrides.set(docSnap.id, rest);
      });
      callback(overrides);
    });
  },
  // Manually-added tickets (no real meeting/transcript behind them) - kept
  // in their own collection rather than mixed into SQLite/data.js, since
  // they need to appear live on any open tab without a republish+push.
  async addManualItem({ meeting_date, owner, topic, description, context }) {
    const ref = await addDoc(collection(db, MANUAL_ITEMS_COLLECTION), {
      meeting_date,
      owner,
      topic: topic || "General",
      description,
      context: context || null,
      status: "open",
      created_at: Date.now(),
    });
    return ref.id;
  },
  subscribeManualItems(callback) {
    return watch(MANUAL_ITEMS_COLLECTION, (snapshot) => {
      const items = [];
      snapshot.forEach((docSnap) => items.push({ id: docSnap.id, ...docSnap.data() }));
      callback(items);
    });
  },
  async updateManualItem(itemId, fields) {
    await setDoc(doc(db, MANUAL_ITEMS_COLLECTION, itemId), fields, { merge: true });
  },
  // The To Do board (2026-08-31) is the first place manual tickets actually
  // need a delete affordance in the UI - also clears any status override so
  // a deleted item's old doc can't resurface if its id is ever reused.
  async deleteManualItem(itemId) {
    await deleteDoc(doc(db, MANUAL_ITEMS_COLLECTION, itemId));
    await deleteDoc(doc(db, STATUS_COLLECTION, itemId)).catch(() => {});
  },
};

// Content calendar - planned/in-progress/published content across blog,
// ebooks, social, sponsor highlights, etc. Same shared-live-state pattern
// as team tickets, own collections so the two never collide on ids.
window.domaContentCalendar = {
  async addItem({ date, type, title, owner, notes, headline, direction, graphic, resource, link }) {
    const ref = await addDoc(collection(db, CALENDAR_ITEMS_COLLECTION), {
      date,
      type: type || "Blog post",
      title,
      owner: owner || null,
      notes: notes || null,
      // The weekly-cadence content-planning fields Juli asked for - all
      // optional, filled in as much or as little as the person adding the
      // item wants at that moment.
      headline: headline || null,
      direction: direction || null,
      graphic: graphic || null,
      resource: resource || null,
      link: link || null,
      // Internal status values stay "open"/"in_progress"/"done" - same enum
      // as team tickets, so the click-to-cycle logic (TEAM_STATUS_ORDER)
      // works unmodified. CALENDAR_STATUS_LABELS maps "open" to "Planned"
      // and "done" to "Published" for display only.
      status: "open",
      created_at: Date.now(),
    });
    return ref.id;
  },
  subscribeItems(callback) {
    return watch(CALENDAR_ITEMS_COLLECTION, (snapshot) => {
      const items = [];
      snapshot.forEach((docSnap) => items.push({ id: docSnap.id, ...docSnap.data() }));
      callback(items);
    });
  },
  async setStatus(itemId, status) {
    await setDoc(doc(db, CALENDAR_STATUS_COLLECTION, String(itemId)), { status, updated_at: Date.now() });
  },
  subscribeStatuses(callback) {
    return watch(CALENDAR_STATUS_COLLECTION, (snapshot) => {
      const statuses = new Map();
      snapshot.forEach((docSnap) => statuses.set(docSnap.id, docSnap.data().status));
      callback(statuses);
    });
  },
  async deleteItem(itemId) {
    await deleteDoc(doc(db, CALENDAR_ITEMS_COLLECTION, itemId));
    await deleteDoc(doc(db, CALENDAR_STATUS_COLLECTION, itemId)).catch(() => {});
  },
  async updateItem(itemId, fields) {
    await setDoc(doc(db, CALENDAR_ITEMS_COLLECTION, itemId), fields, { merge: true });
  },
};

// Useful Links tab - just a shared, organized bookmark list.
window.domaUsefulLinks = {
  async addLink({ title, url, category }) {
    const ref = await addDoc(collection(db, LINKS_COLLECTION), {
      title,
      url,
      category: category || "General",
      created_at: Date.now(),
    });
    return ref.id;
  },
  subscribeLinks(callback) {
    return watch(LINKS_COLLECTION, (snapshot) => {
      const links = [];
      snapshot.forEach((docSnap) => links.push({ id: docSnap.id, ...docSnap.data() }));
      callback(links);
    });
  },
  async deleteLink(linkId) {
    await deleteDoc(doc(db, LINKS_COLLECTION, linkId));
  },
};

// Community member count - GoHighLevel has no public API for its
// Communities feature, so this is a manually-updated number (read off the
// GHL Communities panel by hand) rather than a synced one.
window.domaCommunityStats = {
  async setMemberCount(count) {
    await setDoc(doc(db, COMMUNITY_STATS_COLLECTION, COMMUNITY_STATS_DOC_ID), {
      member_count: count,
      updated_at: Date.now(),
    });
  },
  subscribe(callback) {
    return onSnapshot(
      doc(db, COMMUNITY_STATS_COLLECTION, COMMUNITY_STATS_DOC_ID),
      (docSnap) => callback(docSnap.exists() ? docSnap.data() : null),
      (error) => console.error("Community stats live-sync stopped:", error.code, error.message)
    );
  },
};

window.domaTeamSyncReady = true;
window.dispatchEvent(new Event("doma-team-sync-ready"));
