package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"

	googleplayscraper "github.com/kryuchenko/google-play-scraper"
)

func main() {
	maxApps := flag.Int("max", 300, "hard ceiling on unique apps")
	throttle := flag.Duration("throttle", 400*time.Millisecond, "minimum delay between requests")
	flag.Parse()

	searchTerms := flag.Args()
	if len(searchTerms) == 0 {
		searchTerms = []string{}
	}
	locales := googleplayscraper.CoverageLocales[:1]
	client := googleplayscraper.NewClient(googleplayscraper.WithThrottle(*throttle))
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()

	result, err := client.CategoryApps(ctx, googleplayscraper.CoverageOptions{
		Category: googleplayscraper.CategoryGamePuzzle,
		// Top free, top paid and grossing are the charts the Play job already blocks.
		Collections: []googleplayscraper.Collection{googleplayscraper.CollectionNewFree},
		Locales:     locales,
		SearchTerms: searchTerms,
		GraphDepth:  0,
		MaxApps:     *maxApps,
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "CategoryApps error: %v\n", err)
		os.Exit(1)
	}
	for _, app := range result.Apps {
		row := map[string]any{
			"appId":     app.AppID,
			"title":     app.Title,
			"developer": app.Developer,
			"free":      app.Free,
		}
		encoded, err := json.Marshal(row)
		if err != nil {
			fmt.Fprintf(os.Stderr, "json error: %v\n", err)
			os.Exit(1)
		}
		fmt.Println(string(encoded))
	}
}
