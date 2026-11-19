import {
  Camera,
  Church,
  Landmark,
  Mountain,
  PawPrint,
  ShoppingBag,
  Sparkles,
  Theater,
  Trees,
  Utensils,
  Waves,
  Droplets,
  Flower2,
  type LucideIcon,
} from "lucide-react";
import type { Interest } from "@/types/api";

export interface CategoryMeta {
  label: string;
  blurb: string;
  icon: LucideIcon;
  /** Placeholder tint when a destination has no permitted photo */
  tint: string;
}

export const CATEGORIES: Record<Interest, CategoryMeta> = {
  nature: { label: "Nature", blurb: "Forests, lakes and open views", icon: Trees, tint: "#2e6a4e" },
  adventure: { label: "Adventure", blurb: "Hikes, climbs and trails", icon: Mountain, tint: "#5b4a2e" },
  historical: { label: "Historical", blurb: "Caves, ruins and old towns", icon: Landmark, tint: "#6b4a3a" },
  religious: { label: "Religious", blurb: "Temples, shrines and pagodas", icon: Church, tint: "#7a5a1e" },
  cultural: { label: "Cultural", blurb: "Museums, crafts and galleries", icon: Theater, tint: "#2b5c88" },
  food: { label: "Food", blurb: "Local kitchens and cafes", icon: Utensils, tint: "#8a4b2a" },
  entertainment: { label: "Entertainment", blurb: "Stadiums, parks and shows", icon: Sparkles, tint: "#4a3a6b" },
  shopping: { label: "Shopping", blurb: "Markets, malls and crafts", icon: ShoppingBag, tint: "#3a5a6b" },
  wildlife: { label: "Wildlife", blurb: "Zoos and sanctuaries", icon: PawPrint, tint: "#3e5e2a" },
  photography: { label: "Photography", blurb: "Places people love to photograph", icon: Camera, tint: "#18262e" },
  beaches: { label: "Beaches", blurb: "Sea, sand and sunsets", icon: Waves, tint: "#2b6f88" },
  waterfalls: { label: "Waterfalls", blurb: "Falls and streams", icon: Droplets, tint: "#2b5c88" },
  parks_gardens: { label: "Parks & gardens", blurb: "Green spaces to slow down", icon: Flower2, tint: "#3f7a4a" },
};

export const INTERESTS = Object.keys(CATEGORIES) as Interest[];
export const FEATURED_INTERESTS: Interest[] = [
  "nature", "adventure", "historical", "religious", "cultural", "food", "entertainment", "shopping",
];

export function categoryMeta(category: string): CategoryMeta {
  return CATEGORIES[category as Interest] ?? CATEGORIES.cultural;
}
