/**
 * Home page (root route).
 *
 * Server component: it checks once whether `public/images/hero.jpg` exists so
 * the hero can fall back to a gradient illustration without flashing a broken
 * image. The page content itself is the client `HomeContent`, so its static
 * text follows the UI language selector.
 */

import { existsSync } from "node:fs";
import path from "node:path";
import HomeContent from "./components/HomeContent";

const HERO_IMAGE_PATH = path.join(process.cwd(), "public", "images", "hero.jpg");

export default function HomePage() {
  return <HomeContent hasHeroImage={existsSync(HERO_IMAGE_PATH)} />;
}
