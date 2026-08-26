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
    await setDoc(doc(db, STATUS_COLLECTION, String(itemId)), { status, updated_at: Date.now() });
  },
  subscribeAll(callback) {
    return watch(STATUS_COLLECTION, (snapshot) => {
      const statuses = new Map();
      snapshot.forEach((docSnap) => statuses.set(docSnap.id, docSnap.data().status));
      callback(statuses);
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
};

// Content calendar - planned/in-progress/published content across blog,
// ebooks, social, sponsor highlights, etc. Same shared-live-state pattern
// as team tickets, own collections so the two never collide on ids.
window.domaContentCalendar = {
  async addItem({ date, type, title, owner, notes }) {
    const ref = await addDoc(collection(db, CALENDAR_ITEMS_COLLECTION), {
      date,
      type: type || "Blog post",
      title,
      owner: owner || null,
      notes: notes || null,
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

window.domaTeamSyncReady = true;
window.dispatchEvent(new Event("doma-team-sync-ready"));
