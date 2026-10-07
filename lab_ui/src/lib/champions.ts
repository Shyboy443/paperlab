// Optional display names: each bot can be shown under a League of Legends champion name (the Lovable dashboard's
// naming). PaperLab's own names stay the identity used everywhere else (Telegram, the operator console).
export const CHAMPIONS = [
  "Aatrox", "Ahri", "Akali", "Akshan", "Alistar", "Ambessa", "Amumu", "Anivia", "Annie", "Aphelios", "Ashe", "Aurelion Sol",
  "Aurora", "Azir", "Bard", "Bel'Veth", "Blitzcrank", "Brand", "Braum", "Briar", "Caitlyn", "Camille", "Cassiopeia", "Cho'Gath",
  "Corki", "Darius", "Diana", "Dr. Mundo", "Draven", "Ekko", "Elise", "Evelynn", "Ezreal", "Fiddlesticks", "Fiora", "Fizz",
  "Galio", "Gangplank", "Garen", "Gnar", "Gragas", "Graves", "Gwen", "Hecarim", "Heimerdinger", "Hwei", "Illaoi", "Irelia",
  "Ivern", "Janna", "Jarvan IV", "Jax", "Jayce", "Jhin", "Jinx", "K'Sante", "Kai'Sa", "Kalista", "Karma", "Karthus",
  "Kassadin", "Katarina", "Kayle", "Kayn", "Kennen", "Kha'Zix", "Kindred", "Kled", "Kog'Maw", "LeBlanc", "Lee Sin", "Leona",
  "Lillia", "Lissandra", "Lucian", "Lulu", "Lux", "Malphite", "Malzahar", "Maokai", "Master Yi", "Mel", "Milio", "Miss Fortune",
  "Mordekaiser", "Morgana", "Naafiri", "Nami", "Nasus", "Nautilus", "Neeko", "Nidalee", "Nilah", "Nocturne", "Nunu", "Olaf",
  "Orianna", "Ornn", "Pantheon", "Poppy", "Pyke", "Qiyana", "Quinn", "Rakan", "Rammus", "Rek'Sai", "Rell", "Renata",
  "Renekton", "Rengar", "Riven", "Rumble", "Ryze", "Samira", "Sejuani", "Senna", "Seraphine", "Sett", "Shaco", "Shen",
  "Shyvana", "Singed", "Sion", "Sivir", "Skarner", "Smolder", "Sona", "Soraka", "Swain", "Sylas", "Syndra", "Tahm Kench",
  "Taliyah", "Talon", "Taric", "Teemo", "Thresh", "Tristana", "Trundle", "Tryndamere", "Twisted Fate", "Twitch", "Udyr", "Urgot",
  "Varus", "Vayne", "Veigar", "Vel'Koz", "Vex", "Vi", "Viego", "Viktor", "Vladimir", "Volibear", "Warwick", "Wukong",
  "Xayah", "Xerath", "Xin Zhao", "Yasuo", "Yone", "Yorick", "Yuumi", "Zac", "Zed", "Zeri", "Ziggs", "Zilean", "Zoe", "Zyra",
];

function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** Stable, unique champion per base bot; twins share it with a suffix ("Ahri AI", "Ahri Ladder"). Adds `champion`
 *  and leaves PaperLab's own `name` alone, so the page can show either (see src/lib/names.ts). */
export function withChampions<T extends { id: string }>(bots: T[]): (T & { champion: string })[] {
  const baseOf = (id: string) => id.replace(/\+(JEV|LADDER)$/i, "");
  const used = new Set<number>();
  const byBase = new Map<string, string>();
  for (const base of [...new Set(bots.map((b) => baseOf(b.id)))].sort()) {
    let i = hash(base) % CHAMPIONS.length;
    for (let n = 0; used.has(i) && n < CHAMPIONS.length; n++) i = (i + 1) % CHAMPIONS.length;
    used.add(i);
    byBase.set(base, CHAMPIONS[i]!);
  }
  return bots.map((b) => {
    const champ = byBase.get(baseOf(b.id))!;
    const suffix = /\+JEV$/i.test(b.id) ? " AI" : /\+LADDER$/i.test(b.id) ? " Ladder" : "";
    return { ...b, champion: champ + suffix };
  });
}
