package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	googleplayscraper "github.com/kryuchenko/google-play-scraper"
)

var mechanicTerms = []string{
	"maze",
	"logic puzzle",
	"nonogram",
	"tangram",
	"physics puzzle",
	"sliding puzzle",
	"escape puzzle",
	"pipe puzzle",
	"hidden object puzzle",
	"brain teaser",
}

func main() {
	throttle := flag.Duration("throttle", 400*time.Millisecond, "minimum delay between requests")
	flag.Parse()

	client := googleplayscraper.NewClient(googleplayscraper.WithThrottle(*throttle))
	ctx, cancel := context.WithTimeout(context.Background(), 6*time.Minute)
	defer cancel()

	seen := map[string]bool{}
	var rows []googleplayscraper.SearchResult

	clusters, err := client.ClusterURLs(ctx, googleplayscraper.ClusterURLsOptions{
		Category: googleplayscraper.CategoryGamePuzzle,
		Lang:     "en",
		Country:  "us",
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "ClusterURLs error: %v\n", err)
		os.Exit(1)
	}
	for _, cluster := range clusters {
		if ctx.Err() != nil {
			break
		}
		if skipCluster(cluster.Title) {
			continue
		}
		batch, clusterErr := client.Cluster(ctx, googleplayscraper.ClusterOptions{
			Path:    cluster.URL,
			Lang:    "en",
			Country: "us",
		})
		if clusterErr != nil {
			fmt.Fprintf(os.Stderr, "cluster %s: %v\n", cluster.Title, clusterErr)
			continue
		}
		rows = appendNew(rows, seen, batch)
	}
	for _, term := range mechanicTerms {
		if ctx.Err() != nil {
			break
		}
		batch, searchErr := client.Search(ctx, googleplayscraper.SearchOptions{
			Term:    term,
			Lang:    "en",
			Country: "us",
			Num:     250,
			Price:   "free",
		})
		if searchErr != nil {
			fmt.Fprintf(os.Stderr, "search %s: %v\n", term, searchErr)
			continue
		}
		rows = appendNew(rows, seen, batch)
	}
	for _, app := range rows {
		encoded, err := json.Marshal(map[string]any{
			"appId":     app.AppID,
			"title":     app.Title,
			"developer": app.Developer,
			"summary":   app.Summary,
			"free":      app.Free,
		})
		if err != nil {
			fmt.Fprintf(os.Stderr, "json error: %v\n", err)
			os.Exit(1)
		}
		fmt.Println(string(encoded))
	}
}

func skipCluster(title string) bool {
	name := strings.ToLower(title)
	for _, word := range []string{"top", "grossing", "new"} {
		if strings.Contains(name, word) {
			return true
		}
	}
	return false
}

func appendNew(rows []googleplayscraper.SearchResult, seen map[string]bool, batch []googleplayscraper.SearchResult) []googleplayscraper.SearchResult {
	for _, app := range batch {
		if app.AppID == "" || seen[app.AppID] {
			continue
		}
		seen[app.AppID] = true
		rows = append(rows, app)
	}
	return rows
}
