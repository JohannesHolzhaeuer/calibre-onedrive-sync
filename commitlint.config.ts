/**
 * commitlint configuration.
 *
 * Enforces Conventional Commits, which is also what release-please relies on to
 * determine the next version and to generate the changelog.
 */
import type { UserConfig } from "@commitlint/types";

const config: UserConfig = {
  extends: ["@commitlint/config-conventional"],
  rules: {
    "body-max-line-length": [0, "always", Infinity],
  },
};

export default config;
